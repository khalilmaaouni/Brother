#!/usr/bin/env python3
"""test_cv1_cut_rehearsed: CV1, the cut ships the version it says, rehearsed
(docs/plan/specs/CV1.md). One class per sub unit.

CV1aNextVersion (CV1.a): the next version is the one the version source
(.claude-plugin/marketplace.json, read through scripts/version_source.py)
carries while its tag exists neither in the local tag list nor on the public
remote, the patch bump only when the public remote has it, and NO-DATA for
every state the tag reads cannot tell apart (an unreadable remote, a tag only
in the local list, a remote that hides a local tag or names another
repository, a source older than a public tag, a drifted carrier, an
unreadable source). scripts/next_cut.py --version-only needs no release
policy weekday, scripts/cut.py reads the version through that flag, a real
cut refuses an explicit --version other than the next cut before its
fence, and step 2t of scripts/cut_v1.0.0.sh passes an unchanged note the
way step 2s passes an unchanged bump.

HOW. The version tests read the REAL manifests (version_source.read_source
on this tree) through the new functions, with a scripted runner answering
every tag read (git tag -l, git ls-remote), so nothing reaches the network
and the tests hold for whatever version the tree carries. The drift, the
unreadable source and the cut.main tests run on a copy of the real carriers
in a throwaway folder. The step 2t tests cut the live 2t block out of the
script (between its "== 2t." and "== 3." banners) and run it under `set -e`
in a throwaway git repository with its own HOME, so no machine git config
reaches it.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import json
import math
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CUT_SCRIPT = os.path.join(HERE, "cut_v1.0.0.sh")

sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import retire_catalogs as RC  # noqa: E402
import cut_rehearsal as CR  # noqa: E402
import next_cut as N  # noqa: E402
import version_source as VS  # noqa: E402

#: The version this tree's source carries; every scripted tag answer is
#: built around it, so the tests follow the tree instead of a typed number.
V = VS.read_source(pathlib.Path(ROOT))[0]
HUB_URL = C.EP.DEFAULT_REMOTE
MARKER = "STEP_2T_CONTINUED"


def _key(version):
    return tuple(int(p) for p in version.split("."))


def _below(version):
    major, minor, patch = _key(version)
    if patch:
        return "%d.%d.%d" % (major, minor, patch - 1)
    if minor:
        return "%d.%d.0" % (major, minor - 1)
    return "%d.0.0" % (major - 1)


LOWER = _below(V)
HIGHER = "%d.%d.0" % (_key(V)[0], _key(V)[1] + 1)
BUMPED = N.bump_patch(V)


class Done(object):
    """A process result, the shape subprocess.run returns."""

    def __init__(self, args, returncode, stdout="", stderr=""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TagRunner(object):
    """Answers the git reads tag_state and highest_public_tag make from a
    scripted local tag list and a scripted remote, and records every argv.
    `url`: what `git remote get-url` prints (None: no such remote); `rewrite`:
    what `git ls-remote --get-url` prints for a URL (None: the URL itself).
    An unscripted command answers exit 99, never an answer."""

    def __init__(self, local=(), remote=(), remote_ok=True, local_ok=True,
                 url=None, rewrite=None):
        self.local = list(local)
        self.remote = list(remote)
        self.remote_ok = remote_ok
        self.local_ok = local_ok
        self.url = url
        self.rewrite = rewrite
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:3] == ["git", "ls-remote", "--get-url"]:
            return Done(cmd, 0, (self.rewrite or cmd[3]) + "\n")
        if cmd[:3] == ["git", "remote", "get-url"]:
            if self.url is None:
                return Done(cmd, 2, "", "error: No such remote '%s'" % cmd[3])
            return Done(cmd, 0, self.url + "\n")
        if cmd[:3] == ["git", "tag", "-l"]:
            if not self.local_ok:
                return Done(cmd, 128, "", "fatal: not a git repository")
            # the hub's own list also carries tags of other shapes
            return Done(cmd, 0, "".join("v%s\n" % v for v in self.local)
                        + "brothermode--v3.3.2\npreflight/20260924-124402\n")
        if cmd[:3] == ["git", "ls-remote", "--tags"]:
            if not self.remote_ok:
                return Done(cmd, 128, "", "fatal: unable to access the remote")
            lines = []
            for i, v in enumerate(self.remote):
                lines.append("%040x\trefs/tags/v%s" % (i + 1, v))
                lines.append("%040x\trefs/tags/v%s^{}" % (i + 1001, v))
            lines.append("%s\trefs/tags/brothermode--v9.9.9" % ("f" * 40))
            if len(cmd) > 4:
                lines = [l for l in lines if l.split("\t")[1] == cmd[4]]
            return Done(cmd, 0, "".join(l + "\n" for l in lines))
        return Done(cmd, 99, "", "unscripted command")

    def read(self, verb):
        return [c for c in self.calls if c[:3] == ["git"] + verb]


def in_flight():
    """V is tagged nowhere; the remote and the local list hold LOWER."""
    return TagRunner(local=[LOWER], remote=[LOWER])


def released():
    """V is on the remote and in the local list."""
    return TagRunner(local=[LOWER, V], remote=[LOWER, V])


def run_main(argv, root=ROOT, runner=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = N.main(argv, root=root, runner=runner)
    return code, out.getvalue()


def copy_carriers(dest):
    """The real source and every carrier version_source.run_check reads,
    copied into `dest`."""
    rels = [VS.MARKETPLACE_REL] + list(VS.CARRIER_FILES_REL)
    for product in ("brothermode", "brothersbe"):
        for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                    ".cursor-plugin/plugin.json", "VERSION"):
            rels.append("products/%s/%s" % (product, rel))
    for rel in rels:
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            continue
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(src, target)


class FakeBm(object):
    @staticmethod
    def paths_overlap(a, b):
        return a.rstrip("/") == b.rstrip("/")


class CutRunner(TagRunner):
    """A TagRunner for a whole cut.main: next_cut.py --version-only runs in
    this process on the scripted tag reads (no network), every other command
    is recorded and answers exit 0 with no output."""

    def __init__(self, next_cut_root, **kwargs):
        TagRunner.__init__(self, **kwargs)
        self.next_cut_root = next_cut_root

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        if cmd[:1] == ["git"]:
            return TagRunner.__call__(self, cmd, **kwargs)
        self.calls.append(cmd)
        if len(cmd) > 1 and os.path.basename(cmd[1]) == "next_cut.py":
            code, out = run_main(cmd[2:], root=self.next_cut_root, runner=self)
            return Done(cmd, code, out)
        return Done(cmd, 0, "")

    def claims(self):
        return [c for c in self.calls if len(c) > 2 and c[2] == "claim"]


class CV1aNextVersion(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="cv1a-")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)

    def fixture(self):
        root = os.path.join(self.scratch, "tree")
        copy_carriers(root)
        self.assertEqual(N.carrier_drift(root), "",
                         "the copied carriers must start drift free")
        return root

    # --- next_cut: the version ----------------------------------------------

    def test_the_tree_carries_one_x_y_z_version(self):
        self.assertTrue(N._is_version(V), V)
        self.assertEqual(N.HUB_REPO_PATH, N.hub_repo_path(HUB_URL),
                         "HUB_REPO_PATH must name the repository "
                         "export_public.py publishes to")

    def test_an_untagged_manifest_version_is_the_next_version(self):
        runner = in_flight()
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "absent")
        version, basis = N.derive_next(ROOT, HUB_URL, in_flight())
        self.assertEqual(version, V, basis)
        self.assertTrue(basis.startswith("in-flight:"), basis)
        # the one remote read is the exact URL, never a name
        self.assertTrue(runner.read(["ls-remote", "--tags"]))
        for cmd in runner.read(["ls-remote", "--tags"]):
            self.assertEqual(cmd[3], HUB_URL)

    def test_a_released_manifest_version_bumps_the_patch(self):
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, released()), "present")
        version, basis = N.derive_next(ROOT, HUB_URL, released())
        self.assertEqual(version, BUMPED, basis)
        self.assertTrue(basis.startswith("released:"), basis)

    def test_a_local_only_tag_is_no_data_not_released(self):
        runner = TagRunner(local=[LOWER, V], remote=[LOWER])
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "local-only")
        version, basis = N.derive_next(
            ROOT, HUB_URL, TagRunner(local=[LOWER, V], remote=[LOWER]))
        self.assertIsNone(version, basis)
        self.assertTrue(basis.startswith("NO-DATA:"), basis)
        self.assertIn("local tag list", basis)

    def test_an_unreadable_remote_is_no_data_not_absent(self):
        for local in ([LOWER], [LOWER, V], []):
            runner = TagRunner(local=local, remote=[LOWER], remote_ok=False)
            self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "unknown",
                             local)
        version, basis = N.derive_next(
            ROOT, HUB_URL, TagRunner(local=[LOWER], remote_ok=False))
        self.assertIsNone(version, basis)
        self.assertTrue(basis.startswith("NO-DATA:"), basis)
        code, out = run_main(["--version-only"],
                             runner=TagRunner(local=[LOWER], remote_ok=False))
        self.assertEqual(code, N.EXIT_NODATA, out)
        self.assertNotIn("next cut version", out)

    def test_an_unreadable_local_tag_list_is_unknown(self):
        runner = TagRunner(local=[LOWER], remote=[LOWER], local_ok=False)
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "unknown")

    def test_a_remote_that_hides_a_local_tag_is_unknown(self):
        """The remote answers, but without LOWER, a release tag the local list
        holds: its history (and its highest tag) is falsely low, so it never
        steers derive_next into in-flight."""
        runner = TagRunner(local=[LOWER], remote=[])
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "unknown")
        version, basis = N.derive_next(ROOT, HUB_URL,
                                       TagRunner(local=[LOWER], remote=[]))
        self.assertIsNone(version, basis)
        self.assertTrue(basis.startswith("NO-DATA:"), basis)

    def test_a_remote_naming_another_repository_is_unknown(self):
        hub = TagRunner(local=[LOWER], remote=[LOWER],
                        url="https://github.com/khalilmaaouni/brother-hub.git")
        self.assertEqual(N.tag_state(ROOT, V, "origin", hub), "unknown")
        self.assertEqual(hub.read(["ls-remote", "--tags"]), [],
                         "another repository's tags are never read")
        public = TagRunner(local=[LOWER], remote=[LOWER],
                           url="https://github.com/khalilmaaouni/Brother.git")
        self.assertEqual(N.tag_state(ROOT, V, "origin", public), "absent")
        self.assertEqual(public.read(["ls-remote", "--tags"])[0][3],
                         "https://github.com/khalilmaaouni/Brother.git")
        rewritten = TagRunner(local=[LOWER], remote=[LOWER],
                              rewrite="file:///elsewhere/Brother.git")
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, rewritten), "unknown")
        for url in ("https://example.com/khalilmaaouni/Brother",
                    "/srv/khalilmaaouni/Brother.git",
                    "file:///srv/khalilmaaouni/Brother.git"):
            self.assertEqual(N.tag_state(ROOT, V, url, in_flight()),
                             "unknown", url)
        with self.assertRaises(ValueError):
            N.highest_public_tag(ROOT, "origin", hub)

    def test_a_lagging_manifest_is_no_data(self):
        """The remote already has a tag above the source's version: never a
        version, whether or not the source's own tag is there."""
        self.assertEqual(N.highest_public_tag(
            ROOT, HUB_URL, TagRunner(remote=[LOWER, HIGHER])), HIGHER)
        for remote in ([LOWER, HIGHER], [LOWER, V, HIGHER]):
            local = [LOWER, V] if V in remote else [LOWER]
            version, basis = N.derive_next(
                ROOT, HUB_URL, TagRunner(local=local, remote=remote))
            self.assertIsNone(version, (remote, basis))
            self.assertIn("the manifest is older than a released tag", basis)

    def test_highest_public_tag_reads_only_release_tags(self):
        self.assertEqual(N.highest_public_tag(ROOT, HUB_URL, TagRunner()), "")
        self.assertEqual(N.highest_public_tag(
            ROOT, HUB_URL, TagRunner(remote=["1.0.9", "1.0.10", "0.9.11"])),
            "1.0.10")
        with self.assertRaises(ValueError):
            N.highest_public_tag(ROOT, HUB_URL, TagRunner(remote_ok=False))

        def truncated(cmd, **kwargs):
            if cmd[:3] == ["git", "ls-remote", "--tags"]:
                return Done(cmd, 0, "%s\trefs/tags/v1.0.1\n%s" % ("a" * 40, "b" * 12))
            return TagRunner()(cmd, **kwargs)
        with self.assertRaises(ValueError):
            N.highest_public_tag(ROOT, HUB_URL, truncated)
        self.assertEqual(N.tag_state(ROOT, V, HUB_URL, truncated), "unknown")

    def test_a_drifted_carrier_is_no_data(self):
        root = self.fixture()
        codex = os.path.join(root, "bundle", ".codex-plugin", "plugin.json")
        with open(codex, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["version"] = "0.0.0"
        with open(codex, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        version, basis = N.derive_next(root, HUB_URL, in_flight())
        self.assertIsNone(version, basis)
        self.assertTrue(basis.startswith("NO-DATA:"), basis)
        self.assertIn("drift", basis)
        self.assertIn("bundle/.codex-plugin/plugin.json", basis)

    def test_a_manifest_that_disagrees_with_the_source_is_no_data(self):
        manifest = os.path.join(self.scratch, "plugin.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": LOWER}, fh)
        code, out = run_main(["--version-only", "--manifest", manifest],
                             runner=in_flight())
        self.assertEqual(code, N.EXIT_NODATA, out)
        self.assertIn("a drift", out)
        self.assertNotIn("next cut version", out)

    def test_an_unreadable_version_source_is_no_data(self):
        root = self.fixture()
        with open(os.path.join(root, VS.MARKETPLACE_REL), "w",
                  encoding="utf-8") as fh:
            fh.write('{"plugins": [{"name": "brother", "vers')
        version, basis = N.derive_next(root, HUB_URL, in_flight())
        self.assertIsNone(version, basis)
        # the source itself is named, never a carrier read in its place
        self.assertTrue(basis.startswith("NO-DATA: %s is missing or unreadable"
                                         % VS.MARKETPLACE_REL), basis)
        code, out = run_main(["--version-only"], root=root, runner=in_flight())
        self.assertEqual(code, N.EXIT_NODATA, out)
        self.assertNotIn("next cut version", out)

    def test_version_only_needs_no_weekday(self):
        policy = os.path.join(self.scratch, "RELEASE-POLICY.md")
        with open(policy, "w", encoding="utf-8") as fh:
            fh.write("# Release policy\n\nCuts land whenever a lane finishes.\n")
        code, out = run_main(["--version-only", "--policy", policy],
                             runner=in_flight())
        self.assertEqual(code, N.EXIT_OK, out)
        self.assertIn("next cut version: %s\n" % V, out)
        self.assertNotIn("weekday", out)
        # the path without the flag still needs the weekday, unchanged
        code, out = run_main(["--policy", policy], runner=in_flight())
        self.assertEqual(code, N.EXIT_NODATA, out)

    def test_the_real_manifests_print_their_own_version_while_untagged(self):
        with open(os.path.join(ROOT, "bundle", ".claude-plugin",
                               "plugin.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], V)
        code, out = run_main(["--version-only"], runner=in_flight())
        self.assertEqual(code, N.EXIT_OK, out)
        self.assertIn("next cut version: %s\n" % V, out)
        self.assertIn("next cut basis: in-flight", out)
        self.assertIn("closeout command: python3 scripts/release_closeout.py "
                      "all --version %s" % V, out)
        # and the cut reads exactly that line
        runner = CutRunner(ROOT, local=[LOWER], remote=[LOWER])
        with contextlib.redirect_stdout(io.StringIO()):
            version, _lines = C.read_next_version(ROOT, runner)
        self.assertEqual(version, V)

    def test_read_next_version_passes_version_only(self):
        seen = []

        def runner(cmd, **kwargs):
            seen.append(list(cmd))
            return Done(cmd, 0, "next cut basis: in-flight: x\n"
                                "next cut version: 9.9.9\n")
        version, _lines = C.read_next_version(ROOT, runner)
        self.assertEqual(version, "9.9.9")
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(os.path.basename(seen[0][1]), "next_cut.py")
        self.assertIn("--version-only", seen[0])

    # --- cut.py: the explicit version, refused before the fence -------------

    def test_a_version_that_skips_the_in_flight_one_is_refused(self):
        why = C.version_skip_refusal(ROOT, BUMPED, runner=in_flight())
        self.assertTrue(why.startswith("REFUSED:"), why)
        self.assertIn("skips v%s" % V, why)
        runner = in_flight()
        self.assertEqual(C.version_skip_refusal(ROOT, V, runner=runner), "")
        # the default remote is the one export_public.py publishes to
        self.assertEqual(runner.read(["ls-remote", "--tags"])[0][3], HUB_URL)
        # released: the derived bump skips nothing
        self.assertEqual(C.version_skip_refusal(ROOT, BUMPED, HUB_URL,
                                                released()), "")
        # CV1.e re-based it on derive_next: a version behind the one in
        # flight is refused too, never only a skip ahead
        why = C.version_skip_refusal(ROOT, LOWER, runner=in_flight())
        self.assertTrue(why.startswith("REFUSED:"), why)
        # never a guess: unknown and local-only refuse, explicit or not
        for runner in (TagRunner(local=[LOWER], remote_ok=False),
                       TagRunner(local=[LOWER, V], remote=[LOWER])):
            why = C.version_skip_refusal(ROOT, V, runner=runner)
            self.assertTrue(why.startswith("NO-DATA:"), why)

    def test_cut_gates_refuses_a_redirected_evidence_folder(self):
        for name in C.CUT_REDIRECT_ENV:
            why = C.cut_gates(ROOT, V, runner=in_flight(), env={name: ""})
            self.assertTrue(why.startswith("REFUSED:"), why)
            self.assertIn(name, why)
        # CV1.b reads the rehearsal record next; it is proven by CV1bRehearsal
        with mock.patch.object(C, "rehearsal_refusal", return_value=""), \
                mock.patch.object(CR, "evidence_dir",
                                  return_value=self.scratch):
            self.assertEqual(C.cut_gates(ROOT, V, runner=in_flight(), env={}),
                             "")

    def _cut_main(self, argv, root):
        runner = CutRunner(root, local=[LOWER], remote=[LOWER])
        out = io.StringIO()
        del C._BM_STORE_CACHE[:]
        env = dict((k, v) for k, v in os.environ.items()
                   if k not in C.CUT_REDIRECT_ENV)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(C, "_load_bm_store",
                                  return_value=(FakeBm, "/fake/bm_store.py",
                                                None)), \
                mock.patch.object(C, "_stdin_is_tty", return_value=True), \
                mock.patch.object(CR, "evidence_dir",
                                  return_value=self.scratch), \
                contextlib.redirect_stdout(out):
            code = C.main(argv + ["--no-release-page", "a test of the gates"],
                          root=root, runner=runner,
                          ask=lambda prompt: "n")
        del C._BM_STORE_CACHE[:]
        return code, out.getvalue(), runner

    def test_cut_main_refuses_an_explicit_skipping_version_before_the_fence(self):
        root = self.fixture()
        code, out, runner = self._cut_main(["--version", BUMPED], root)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("skips v%s" % V, out)
        self.assertEqual(runner.claims(), [], "no fence claim may be made")
        self.assertNotIn("cut_v1.0.0.sh", " ".join(" ".join(c) for c in runner.calls))
        # CV1.e: an explicit version equal to derive_next's answer passes the
        # version gate (R-CVE-07); the rehearsal gate is the next refusal
        with mock.patch.object(C, "rehearsal_refusal", return_value=""):
            code, out, runner = self._cut_main(["--version", V], root)
        self.assertNotIn("skips", out)
        self.assertEqual(len(runner.claims()), 1, out)

    def test_a_derived_version_passes_the_gates_to_the_fence(self):
        root = self.fixture()
        with mock.patch.object(C, "rehearsal_refusal", return_value=""):
            code, out, runner = self._cut_main([], root)
        self.assertIn("version %s (from scripts/next_cut.py)" % V, out)
        self.assertIn("--version-only", runner.calls[0])
        self.assertEqual(len(runner.claims()), 1, out)
        self.assertIn("release-cut-%s" % V, runner.claims()[0])

    # --- hostile input ------------------------------------------------------

    def test_hostile_input_is_refused_never_a_crash(self):
        bad = [None, 7, 1.5, float("nan"), True, [], ["1.1.0"], {}, b"1.1.0",
               "", "1.1", "1.1.0\n", " 1.1.0", "v1.1.0", "1.1.0-rc.1"]
        for value in bad:
            self.assertEqual(N.tag_state(ROOT, value, HUB_URL, in_flight()),
                             "unknown", value)
            self.assertEqual(N.tag_state(value, V, HUB_URL, in_flight()),
                             "unknown", value)
            self.assertEqual(N.tag_state(ROOT, V, value, in_flight()),
                             "unknown", value)
            self.assertEqual(N.hub_repo_path(value), "", value)
            version, basis = N.derive_next(value, HUB_URL, in_flight())
            self.assertIsNone(version, value)
            self.assertTrue(basis.startswith("NO-DATA:"), (value, basis))
            version, basis = N.derive_next(ROOT, value, in_flight())
            self.assertIsNone(version, value)
            with self.assertRaises(ValueError):
                N.highest_public_tag(ROOT, value, in_flight())
            why = C.version_skip_refusal(ROOT, value, runner=in_flight())
            self.assertTrue(why.startswith("NO-DATA:"), (value, why))
            if value != "":   # "" is the documented default remote
                why = C.version_skip_refusal(ROOT, V, value, in_flight())
                self.assertTrue(why.startswith("NO-DATA:"), (value, why))
        self.assertTrue(math.isnan(bad[3]))
        for env in (["BROTHER_CUT_TEST"], "BROTHER_CUT_TEST", 7, True):
            why = C.cut_gates(ROOT, V, runner=in_flight(), env=env)
            self.assertTrue(why.startswith("REFUSED:"), (env, why))

        # a runner that answers garbage is unknown, never an answer
        def returns_none(cmd, **kwargs):
            return None

        def bool_exit(cmd, **kwargs):
            return Done(cmd, False, "")

        def raises_type_error(cmd, **kwargs):
            raise TypeError("not a process")

        def bytes_out(cmd, **kwargs):
            return Done(cmd, 0, b"")
        for runner in (returns_none, bool_exit, raises_type_error, bytes_out):
            self.assertEqual(N.tag_state(ROOT, V, HUB_URL, runner), "unknown",
                             runner.__name__)
            version, _basis = N.derive_next(ROOT, HUB_URL, runner)
            self.assertIsNone(version, runner.__name__)

    # --- cut_v1.0.0.sh step 2t ----------------------------------------------

    def _repo(self):
        repo = os.path.join(self.scratch, "repo")
        home = os.path.join(self.scratch, "home")
        os.makedirs(repo)
        os.makedirs(home)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        self.env = env
        self.repo = repo
        self.git("init", "-q")
        with open(os.path.join(repo, "note.md"), "w", encoding="utf-8") as fh:
            fh.write("note before\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return repo

    def git(self, *args):
        proc = C._run(["git"] + list(args), self.repo, env=self.env)
        self.assertEqual(proc.returncode, 0, (args, proc.stdout, proc.stderr))
        return proc.stdout

    def run_2t(self):
        with open(CUT_SCRIPT, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        start = [i for i, l in enumerate(lines) if l.startswith('echo "== 2t.')]
        end = [i for i, l in enumerate(lines) if l.startswith('echo "== 3.')]
        self.assertEqual((len(start), len(end)), (1, 1))
        block = lines[start[0]:end[0]]
        self.assertTrue(any("git commit" in l for l in block), block)
        script = os.path.join(self.scratch, "step_2t.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("set -e\nVERSION=%s\nstep_clock() { :; }\n%s\necho %s\n"
                     % (V, "\n".join(block), MARKER))
        return C._run(["sh", script], self.repo, env=self.env)

    def commits(self):
        return self.git("rev-list", "--count", "HEAD").strip()

    def test_step_2t_passes_when_nothing_changed(self):
        self._repo()
        proc = self.run_2t()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(MARKER, proc.stdout)
        self.assertIn("already at %s, nothing to commit at step 2t" % V,
                      proc.stdout)
        self.assertEqual(self.commits(), "1")

    def test_step_2t_commits_a_changed_note(self):
        self._repo()
        with open(os.path.join(self.repo, "note.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("note after\n")
        proc = self.run_2t()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(MARKER, proc.stdout)
        self.assertEqual(self.commits(), "2")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(),
                         "%s: the export manifest that describes the tree "
                         "and the note it ships" % V)
        self.assertNotIn("nothing to commit", proc.stdout)

    def test_step_2t_still_stops_when_a_commit_fails(self):
        self._repo()
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho refused by the hook >&2\nexit 1\n")
        os.chmod(hook, stat.S_IRWXU)
        with open(os.path.join(self.repo, "note.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("note after\n")
        proc = self.run_2t()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(MARKER, proc.stdout)
        self.assertNotIn("nothing to commit", proc.stdout)
        self.assertEqual(self.commits(), "1")


# --- CV1.b: the cut rehearsal and the record the real cut reads ---------------

RV = "1.1.0"
CUT_EMAIL = "cut@example.test"
BUMP = "%s: the version bump and the regenerated manifests" % RV
NOTE = "%s: the export manifest that describes the tree and the note it ships" % RV
OWN = ("bundle/", "docs/releases/", "products/")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Rig(object):
    """A scripted runner: git runs for real in the throwaway repository (through cut._run), except the worktree
    add and remove, which are only recorded; the child cut.py answers a scripted exit and text."""

    def __init__(self, code=0, out="", add_fails=False, fail=()):
        self.code, self.out, self.add_fails, self.fail = code, out, add_fails, set(fail)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append((cmd, kwargs.get("cwd"), kwargs.get("env")))
        if cmd[0] != "git":
            return Done(cmd, self.code, self.out, "")
        if cmd[1] in self.fail:
            return Done(cmd, 128, "", "fatal: scripted failure")
        if cmd[1:3] == ["worktree", "add"]:
            if self.add_fails:
                return Done(cmd, 128, "", "fatal: scripted add failure")
            os.makedirs(cmd[-2])
            return Done(cmd, 0)
        if cmd[1:3] == ["worktree", "remove"]:
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return Done(cmd, 0)
        return C._run(cmd, kwargs.get("cwd"), timeout=kwargs.get("timeout"))

    def of(self, *head):
        return [c for c in self.calls if c[0][:len(head)] == list(head)]


class CV1bRehearsal(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="cv1b-")
        self.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = os.path.join(base, "repo")
        self.ev = os.path.join(base, "evidence")
        self.bs = os.path.join(base, "scratch")
        home = os.path.join(base, "home")
        for d in (self.repo, home, self.bs):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL=CUT_EMAIL,
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL=CUT_EMAIL, BROTHER_SCRATCH=self.bs,
                   BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q")
        self.git("config", "user.email", CUT_EMAIL)
        files = {"scripts/cut.py": "cut\n", "scripts/keep.py": "k\n", "bundle/a.json": "{}\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        self.base_sha = self.commit("base", files)
        self.now = time.time()
        self.n = 0

    def git(self, *args):
        proc = C._run(["git"] + list(args), self.repo)
        self.assertEqual(proc.returncode, 0, (args, proc.stdout, proc.stderr))
        return proc.stdout.strip()

    def commit(self, subject, files, author=None):
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        self.git("add", "-A")
        extra = ["--author=x <%s>" % author] if author else []
        self.git("commit", "-q", "-m", subject, *extra)
        return self.git("rev-parse", "HEAD")

    def record(self, candidate=None, verdict="READY", now=None, trees=None, cut_sha=None,
               log="READY for %s\n" % RV):
        candidate = candidate or self.git("rev-parse", "HEAD")
        self.n += 1
        os.makedirs(self.ev, exist_ok=True)
        log_path = os.path.join(self.ev, "log-%d.txt" % self.n)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(log)
        real, why = CR._script_trees(self.repo, candidate)
        self.assertEqual(why, "")
        real.update(trees or {})
        return CR.record_rehearsal(RV, candidate, self.git("rev-parse", candidate + "^{tree}"),
                                   0 if verdict == "READY" else 1, verdict, log_path, 1.5,
                                   cut_sha or CR.running_cut_py_sha256(), self.ev,
                                   now=self.now if now is None else now, script_trees=real)

    def refusal(self, **kwargs):
        kwargs.setdefault("now", self.now)
        return CR.rehearsal_refusal(self.repo, RV, directory=self.ev, **kwargs)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def cut_chain(self, bump_files=None, note_files=None, author=None, subjects=(BUMP, NOTE)):
        """The cut's commits after the candidate; returns the candidate."""
        candidate = self.head()
        self.commit(subjects[0], bump_files or {"bundle/a.json": '{"v": 1}\n'}, author)
        if len(subjects) > 1:
            self.commit(subjects[1], note_files or {"docs/releases/n.md": "note\n"}, author)
        return candidate

    # --- the run ---------------------------------------------------------------

    def test_a_timed_out_rehearsal_child_dies_with_its_descendants_and_keeps_its_output(self):
        # review 2026-10-04: subprocess.run killed only the child; a grandchild in its own session outlived it
        import cut_rehearsal as R, subprocess as sp, time as t
        try:   # inside the landing sandbox ps cannot run at all (execvp: Operation not permitted): no tree to read
            listed = str(os.getpid()) in __import__("subprocess").run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True, text=True, timeout=10).stdout
        except OSError:
            listed = False
        if not listed:
            self.skipTest("NO-DATA: no process listing here (sandboxed), so a descendant cannot be found or killed")
        d = tempfile.mkdtemp(prefix="cvb-tree-"); self.addCleanup(shutil.rmtree, d, True)
        pidf = os.path.join(d, "pid")
        code = ("import subprocess, sys, time; g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "start_new_session=True); open(sys.argv[1], 'w').write(str(g.pid)); print('started', flush=True); time.sleep(60)")
        r = R._run_tree([sys.executable, "-c", code, pidf], d, dict(os.environ), 2)
        self.assertEqual(r.returncode, 124)
        self.assertIn("started", r.stdout)
        self.assertIn("NO-DATA: timed out after 2s", r.stderr)
        pid = int(open(pidf).read())
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            t.sleep(0.1)
        else:
            os.kill(pid, 9); self.fail("the grandchild outlived the timeout")

    def test_a_background_child_in_the_group_dies_when_the_check_returns(self):
        # review round 10 finding 1: a normal return must not leave a background child in the child's group running
        import cut_rehearsal as R, time as t
        d = tempfile.mkdtemp(prefix="cvb-bg-"); self.addCleanup(shutil.rmtree, d, True)
        pidf = os.path.join(d, "pid")
        code = ("import subprocess, sys; g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "open(sys.argv[1], 'w').write(str(g.pid))")
        r = R._run_tree([sys.executable, "-c", code, pidf], d, dict(os.environ), 30)
        self.assertEqual(r.returncode, 0)
        pid = int(open(pidf).read())
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            t.sleep(0.1)
        else:
            os.kill(pid, 9); self.fail("a background child in the group outlived the check")

    def test_git_calls_are_bounded(self):
        import cut_rehearsal as R
        seen = {}
        def runner(cmd, **kw):
            seen["timeout"] = kw.get("timeout"); return __import__("subprocess").CompletedProcess(cmd, 0, "", "")
        R._sh(["git", "status"], ".", runner)
        self.assertEqual(seen["timeout"], R.GIT_TIMEOUT_S)

    def test_main_refuses_an_argv_that_is_not_a_list_of_str(self):
        # the landing fuzz's own hostile values (2026-10-04): each is a ValueError, the documented refusal, never TypeError
        import cut_rehearsal as R
        for bad in (0, -1, True, float("nan"), b"x", {1, 2}, object(), "--version", ["--version", 1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                R.main(bad)

    def test_exit_zero_without_the_verdict_line_is_no_data(self):
        for out in ("fences clear, but no verdict line\n", "NOT READY for %s: x\n" % RV, "READY for 1.1.00\n"):
            verdict, code, path = CR.run_rehearsal(self.repo, RV, runner=Rig(0, out))
            self.assertEqual((verdict, code), ("NO-DATA", 0), out)
            self.assertEqual(_load(path)["verdict"], "NO-DATA")
        verdict = CR.run_rehearsal(self.repo, RV, runner=Rig(0, "x\nREADY for %s: ok\n" % RV))[0]
        self.assertEqual(verdict, "READY")
        self.assertEqual(CR.run_rehearsal(self.repo, RV, runner=Rig(1, "NOT READY for %s\n" % RV))[0], "NOT-READY")
        for bad in (2, 124, 127, 130):
            got = CR.run_rehearsal(self.repo, RV, runner=Rig(bad, "READY for %s\n" % RV))[0]
            self.assertEqual(got, "NO-DATA", bad)

    def test_run_rehearsal_uses_a_detached_worktree_under_the_scratch_root_and_runs_the_child_there(self):
        rig = Rig(0, "READY for %s\n" % RV)
        verdict, _code, path = CR.run_rehearsal(self.repo, RV, runner=rig)
        self.assertEqual(verdict, "READY")
        add = rig.of("git", "worktree", "add", "--detach")
        self.assertEqual(len(add), 1, rig.calls)
        wt = add[0][0][4]
        self.assertTrue(CR._inside(wt, self.bs), wt)
        self.assertEqual(add[0][0][5], self.head())
        child = [c for c in rig.calls if c[0][0] != "git"]
        self.assertEqual(len(child), 1)
        argv, cwd, env = child[0]
        self.assertEqual(cwd, wt)
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.repo))
        self.assertEqual(argv[1:], [os.path.join(wt, "scripts", "cut.py"), "--check", "--version", RV])
        self.assertTrue(CR._inside(env["TMPDIR"], self.bs), env["TMPDIR"])
        record = _load(path)
        self.assertEqual(record["schema"], CR.SCHEMA)
        self.assertEqual(record["candidate_sha"], self.head())
        self.assertEqual(sorted(record["script_trees"]), sorted(CR.SCRIPT_DIRS))
        self.assertEqual(record["cut_py_sha256"], CR.hashlib.sha256(b"cut\n").hexdigest())
        self.assertTrue(os.path.basename(path).startswith("rehearsal-%s-%s-" % (RV, self.head()[:12])))

    def test_main_prints_the_last_line_and_exits_with_the_verdict(self):
        for code, out, shown, exit_code in ((0, "READY for %s\n" % RV, "READY", 0), (1, "no\n", "NOT-READY", 1),
                                            (0, "no line\n", "NO-DATA", 2)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                got = CR.main(["--version", RV], root=self.repo, runner=Rig(code, out))
            self.assertEqual(got, exit_code)
            last = buf.getvalue().strip().splitlines()[-1]
            self.assertTrue(last.startswith("REHEARSAL %s %s %s exit %d record " % (
                shown, RV, self.head()[:12], code)), last)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(CR.main(["--version", "1.1"], root=self.repo, runner=Rig()), 2)

    def test_only_the_created_worktree_is_removed(self):
        rig = Rig(0, "READY for %s\n" % RV)
        CR.run_rehearsal(self.repo, RV, runner=rig)
        add = rig.of("git", "worktree", "add")[0][0][4]
        removes = rig.of("git", "worktree", "remove")
        self.assertEqual([r[0][-1] for r in removes], [add])
        self.assertFalse(os.path.exists(add))
        rig = Rig(0, "x", add_fails=True)
        verdict, _code, path = CR.run_rehearsal(self.repo, RV, runner=rig)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(rig.of("git", "worktree", "remove"), [])
        self.assertIn("worktree add failed", _load(path)["note"])
        outside = os.path.join(self.base, "precious")
        os.makedirs(outside)
        rig = Rig()
        self.assertFalse(CR._remove_worktree(self.repo, outside, self.bs, rig))
        self.assertFalse(CR._remove_worktree(self.repo, self.bs, self.bs, rig))
        self.assertFalse(CR._remove_worktree(self.repo, os.path.join(self.bs, "..", "precious"), self.bs, rig))
        self.assertEqual(rig.calls, [])

    def test_a_second_rehearsal_refuses(self):
        lock = os.path.join(self.bs, "cut-rehearsal.lock")
        with open(lock, "w") as fh:
            fh.write("%d\n" % os.getpid())
        rig = Rig(0, "READY for %s\n" % RV)
        with self.assertRaises(CR.RehearsalRefused):
            CR.run_rehearsal(self.repo, RV, runner=rig)
        self.assertEqual(rig.of("git", "worktree"), [])
        self.assertTrue(os.path.exists(lock))
        with open(lock, "w") as fh:
            fh.write("not a pid\n")
        with self.assertRaises(CR.RehearsalRefused):
            CR.run_rehearsal(self.repo, RV, runner=Rig())
        os.remove(lock)
        CR.run_rehearsal(self.repo, RV, runner=Rig(0, "READY for %s\n" % RV))
        self.assertFalse(os.path.exists(lock))

    # --- the record the real cut reads --------------------------------------------

    def test_a_covering_record_is_accepted_and_the_command_verifies_it(self):
        path = self.record()
        self.assertEqual(self.refusal(), "")
        self.assertEqual(CR.covering_record(self.repo, RV, directory=self.ev, now=self.now), (path, ""))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(C, "_cut_own_paths", return_value=OWN):
            self.assertEqual(CR.main(["--verify", "--version", RV], root=self.repo), 0)
        self.assertEqual(buf.getvalue().strip(), "COVERED")
        self.commit("one more", {"docs/x.md": "x\n"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(CR.main(["--verify", "--version", RV], root=self.repo), 1)
        self.assertNotEqual(buf.getvalue().strip(), "COVERED")

    def test_a_real_cut_without_a_record_refuses_before_the_fence(self):
        rig = Rig()
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith("BROTHER_CUT_"))
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(CR, "evidence_dir", return_value=self.ev), \
                mock.patch.object(C, "version_skip_refusal", return_value=""):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = C.cut_mode(self.repo, RV, (FakeBm, "bm", ""), ["scripts/"], None, True, None, runner=rig)
            self.assertEqual(code, C.EXIT_REFUSED)
            self.assertIn("rehearsal", buf.getvalue())
            self.assertEqual(buf.getvalue().count("\n"), 1, buf.getvalue())
            self.assertEqual([c for c in rig.calls if "claim" in c[0]], [])
            self.record()
            self.assertEqual(C.cut_gates(self.repo, RV, runner=rig, env={}), "")

    def test_a_record_for_another_commit_refuses(self):
        self.record()
        self.assertEqual(self.refusal(), "")
        self.commit("another commit", {"docs/x.md": "x\n"})
        reason = self.refusal()
        self.assertIn("covered", reason)
        self.assertEqual(self.refusal(resume_sha=""), reason)

    def test_resume_accepts_the_cut_s_own_two_commits_and_either_alone(self):
        self.record()
        self.cut_chain()
        self.assertNotEqual(self.refusal(), "")
        self.assertEqual(self.refusal(resume_sha=self.head(), allowed_paths=OWN), "")
        self.assertNotEqual(self.refusal(resume_sha=self.head()), "")
        for subjects in ((BUMP,), (NOTE,)):
            self.git("reset", "-q", "--hard", self.base_sha)
            self.cut_chain(subjects=subjects)
            self.assertEqual(self.refusal(resume_sha=self.head(), allowed_paths=OWN), "", subjects)

    def test_resume_refuses_an_unrelated_commit_after_the_record(self):
        self.record()
        self.cut_chain(subjects=(BUMP, "an unrelated change"))
        self.assertIn("not the cut's own", self.refusal(resume_sha=self.head(), allowed_paths=OWN))
        self.git("reset", "-q", "--hard", self.base_sha)
        self.cut_chain(subjects=(NOTE, BUMP))
        self.assertNotEqual(self.refusal(resume_sha=self.head(), allowed_paths=OWN), "")
        self.git("reset", "-q", "--hard", self.base_sha)
        self.cut_chain()
        self.commit("%s: a third commit" % RV, {"docs/y.md": "y\n"})
        self.assertIn("commits sit between", self.refusal(resume_sha=self.head(), allowed_paths=OWN))

    def test_resume_refuses_a_range_git_cannot_list(self):
        self.record()
        self.cut_chain()
        head = self.head()
        self.assertEqual(self.refusal(resume_sha=head, allowed_paths=OWN), "")
        self.assertIn("cannot be listed", self.refusal(resume_sha=head, allowed_paths=OWN,
                                                       runner=Rig(fail=("rev-list",))))
        self.assertNotEqual(self.refusal(resume_sha=head, allowed_paths=OWN, runner=Rig(fail=("merge-base",))), "")

    def test_resume_refuses_a_commit_by_another_author(self):
        self.record()
        self.cut_chain(author="someone-else@example.test")
        self.assertIn("authored by", self.refusal(resume_sha=self.head(), allowed_paths=OWN))

    def test_resume_refuses_a_change_under_the_script_directories(self):
        candidate = self.record() and self.cut_chain(bump_files={"scripts/keep.py": "changed\n"})
        allowed = OWN + ("scripts/",)
        self.assertIn("scripts changed", CR.resume_chain_problem(self.repo, candidate, self.head(), RV, allowed))
        self.assertIn("scripts changed", self.refusal(resume_sha=self.head(), allowed_paths=allowed))

    def test_a_changed_tools_directory_refuses(self):
        self.record()
        self.cut_chain(bump_files={"products/brothermode/tools/m.py": "changed\n"})
        reason = self.refusal(resume_sha=self.head(), allowed_paths=OWN)
        self.assertIn("products/brothermode/tools changed", reason)
        self.assertIn("products/brothermode/tools", CR.SCRIPT_DIRS)
        self.assertIn("products/brothersbe/tools", CR.SCRIPT_DIRS)

    # --- the cut's own re-pin. 2026-10-07: the first real cut under this rule passed a green chain and stopped
    # before its question, because step 1b of scripts/cut_v1.0.0.sh edits five files this rule did not know.
    # Every case runs the REAL step 1b, read out of the real script, on the real five files.

    STRICT = ("bundle/", "docs/releases/")
    OLD_PIN = "v0.0.9"

    def repin_base(self):
        """Commits the repository's real re-pin files, their pin moved back to OLD_PIN so the case reads the same
        whatever tag the tree is pinned to today."""
        with open(os.path.join(ROOT, CR.REPIN_FACTS), encoding="utf-8") as fh:
            current = CR._PIN_RE.search(fh.read()).group(1)
        files = {}
        for rel in (CR.REPIN_FACTS,) + CR.REPIN_PAGES:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                files[rel] = fh.read().replace(current, self.OLD_PIN)
        self.base_sha = self.commit("the re-pin files as the repository holds them", files)

    def real_step_1b(self, where=None, spoil=None):
        """Runs the real step 1b in `where`, lets `spoil` (path -> new text, or callable on the old text) damage the
        result, then makes the cut's two commits there."""
        import subprocess
        where = where or self.repo
        with open(os.path.join(ROOT, "scripts", "cut_v1.0.0.sh"), encoding="utf-8") as fh:
            body = fh.read().split("python3 - <<'REPIN'\n", 1)[1].split("\nREPIN\n", 1)[0]
        proc = subprocess.run([sys.executable, "-c", body], cwd=where, env=dict(os.environ, VERSION=RV),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PUBLIC_INSTALL_TAG v%s (was %s)" % (RV, self.OLD_PIN), proc.stdout)
        for rel, change in (spoil or {}).items():
            path = os.path.join(where, rel)
            text = ""
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(change(text) if callable(change) else change)
        note = os.path.join(where, "docs", "releases", "n.md")
        os.makedirs(os.path.dirname(note), exist_ok=True)
        for subject, touch in ((BUMP, None), (NOTE, note)):
            if touch:
                with open(touch, "w", encoding="utf-8") as fh:
                    fh.write("note\n")
            for args in (("add", "-A"), ("commit", "-q", "-m", subject)):
                done = C._run(["git"] + list(args), where)
                self.assertEqual(done.returncode, 0, (args, done.stdout, done.stderr))

    def repinned(self, spoil=None, **record):
        """Base, record, the real step with `spoil`; returns the refusal under the strict allowed paths."""
        self.repin_base()
        self.record(**record)
        self.candidate = self.head()
        self.real_step_1b(spoil=spoil)
        return self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT)

    def test_the_real_repin_step_is_the_cut_s_own(self):
        self.assertEqual(self.repinned(), "")
        head = self.head()
        self.assertEqual(CR.resume_chain_problem(self.repo, self.candidate, head, RV, self.STRICT), "")
        self.assertEqual(CR.repinned_paths(self.repo, self.candidate, head, RV),
                         frozenset((CR.REPIN_FACTS,) + CR.REPIN_PAGES))

    def test_a_second_edit_in_the_facts_module_refuses(self):
        reason = self.repinned(spoil={CR.REPIN_FACTS: lambda text: text + "EXTRA = 1\n"})
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_pin_moved_to_another_version_refuses(self):
        reason = self.repinned(spoil={CR.REPIN_FACTS: lambda text: text.replace('"v%s"' % RV, '"v9.9.9"')})
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_page_changed_beyond_its_pin_refuses(self):
        page = CR.REPIN_PAGES[0]
        self.assertIn(page, self.repinned(spoil={page: lambda text: text + "one more sentence\n"}))

    def test_pages_moved_while_the_constant_stayed_refuse(self):
        reason = self.repinned(spoil={CR.REPIN_FACTS: lambda text: text.replace('"v%s"' % RV, '"%s"' % self.OLD_PIN)})
        self.assertIn("not one of the cut's own files", reason)
        self.assertEqual(CR.repinned_paths(self.repo, self.candidate, self.head(), RV), frozenset())

    def test_another_tools_file_beside_the_repin_refuses(self):
        reason = self.repinned(spoil={"products/brothermode/tools/m.py": "changed\n"})
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_record_naming_another_tools_tree_refuses_even_with_the_repin(self):
        reason = self.repinned(trees={"products/brothermode/tools": "0" * 40})
        self.assertIn("changed since the rehearsal", reason)

    def test_a_repin_file_git_cannot_show_refuses(self):
        self.assertEqual(self.repinned(), "")
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT, runner=Rig(fail=("show",)))
        self.assertIn("products/brothermode/tools changed", reason)

    def test_the_rehearsal_is_not_ready_when_head_cannot_be_read(self):
        def runner(cmd, **kw):
            if list(cmd[:3]) == ["git", "rev-parse", "HEAD"]:
                return Done(cmd, 128, "", "fatal: scripted failure")
            return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"), env=kw.get("env"))
        code, out = self.check(runner=runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("HEAD cannot be read", out)

    def test_the_rehearsal_is_not_ready_when_the_chain_leaves_an_uncommitted_change(self):
        code, out = self.check(leave="bundle/a.json")
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("uncommitted changes the cut would refuse to push", out)
        self.assertIn("bundle/a.json", out)

    def test_a_repin_file_made_executable_refuses(self):
        self.repin_base()
        self.record()
        self.candidate = self.head()
        os.chmod(os.path.join(self.repo, CR.REPIN_FACTS), 0o755)
        self.real_step_1b()
        self.assertEqual(self.git("ls-tree", "HEAD", "--", CR.REPIN_FACTS).split(" ", 1)[0], "100755")
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT)
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_file_mode_git_cannot_read_refuses(self):
        # only ls-tree fails here: the trees and the contents still read, so nothing but the mode guard can refuse
        self.assertEqual(self.repinned(), "")
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT, runner=Rig(fail=("ls-tree",)))
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_rename_into_an_allowed_folder_does_not_hide_the_deletion(self):
        self.commit("a file outside the cut's own paths", {"docs/CHARTER.md": "charter\n" * 40})
        self.record()
        candidate = self.head()
        os.makedirs(os.path.join(self.repo, "docs", "releases"), exist_ok=True)
        self.git("mv", "docs/CHARTER.md", "docs/releases/CHARTER.md")
        self.git("commit", "-q", "-m", BUMP)
        self.commit(NOTE, {"docs/releases/n.md": "note\n"})
        self.assertEqual(self.git("diff", "--name-only", "%s..HEAD" % candidate).split(),
                         ["docs/releases/CHARTER.md", "docs/releases/n.md"], "git no longer hides the source of a rename")
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT)
        self.assertIn("docs/CHARTER.md", reason)

    # --- second review, 2026-10-07: objects not text, NUL separated names, the folder listing, the record's trees

    def test_one_more_newline_at_the_end_of_the_facts_module_refuses(self):
        self.assertIn("products/brothermode/tools changed", self.repinned(spoil={CR.REPIN_FACTS: lambda text: text + "\n"}))

    def test_other_line_endings_in_a_repin_file_refuse(self):
        reason = self.repinned(spoil={CR.REPIN_FACTS: lambda text: text.replace("\n", "\r\n")})
        self.assertIn("products/brothermode/tools changed", reason)
        # the object really holds the other endings (a text read hides them): one byte more per line
        sizes = [int(self.git("cat-file", "-s", "%s:%s" % (ref, CR.REPIN_FACTS))) for ref in (self.candidate, "HEAD")]
        self.assertGreater(sizes[1], sizes[0] + 50)

    def test_a_replaced_object_at_the_rehearsed_commit_cannot_hide_a_smuggled_line(self):
        smuggled = "import os  # smuggled\n"
        self.repin_base()
        self.record()
        self.candidate = self.head()
        honest = self.git("rev-parse", "HEAD:" + CR.REPIN_FACTS)
        with open(os.path.join(self.repo, CR.REPIN_FACTS), encoding="utf-8") as fh:
            base_text = fh.read()
        evil = C._run(["git", "hash-object", "-w", "--stdin"], self.repo, stdin_text=base_text + smuggled)
        self.assertEqual(evil.returncode, 0, evil.stderr)
        self.real_step_1b(spoil={CR.REPIN_FACTS: lambda text: text + smuggled})
        self.git("replace", honest, evil.stdout.strip())
        # with the replacement read, step 1b's rewrite of the "rehearsed" text IS the smuggled HEAD
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT)
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_file_name_holding_a_line_separator_is_one_name(self):
        self.git("config", "core.quotePath", "false")
        name = CR.REPIN_FACTS + chr(0x2028) + CR.REPIN_FACTS
        reason = self.repinned(spoil={name: "import os  # smuggled\n"})
        self.assertIn("products/brothermode/tools changed", reason)

    def test_a_scripts_change_refuses_when_its_folder_cannot_be_listed(self):
        def runner(cmd, **kw):
            if list(cmd[:2]) == ["git", "diff"] and "--" in cmd:
                return Done(cmd, 124, "", "scripted: timed out")
            return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"), env=kw.get("env"))
        allowed = ("scripts/",) + self.STRICT   # the real cut allows scripts/, so the folder check is the only net
        self.repin_base()
        self.record()
        self.real_step_1b(spoil={"scripts/keep.py": "changed\n"})
        self.assertIn("scripts changed", self.refusal(resume_sha=self.head(), allowed_paths=allowed, runner=runner))

    def test_a_record_naming_the_tree_at_head_refuses(self):
        self.repin_base()
        self.candidate = self.head()
        self.real_step_1b()
        tools = "products/brothermode/tools"
        self.record(candidate=self.candidate, trees={tools: self.git("rev-parse", "HEAD:" + tools)})
        reason = self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT)
        self.assertIn("changed since the rehearsal", reason)

    def test_a_repin_page_that_does_not_decode_refuses_in_words(self):
        self.repin_base()
        page = CR.REPIN_PAGES[1]
        with open(os.path.join(self.repo, page), "ab") as fh:
            fh.write(b"\xff\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a page that is not UTF-8")
        self.record()
        facts = os.path.join(self.repo, CR.REPIN_FACTS)
        with open(facts, encoding="utf-8") as fh:
            text = fh.read()
        with open(facts, "w", encoding="utf-8") as fh:
            fh.write(text.replace('"%s"' % self.OLD_PIN, '"v%s"' % RV))
        with open(os.path.join(self.repo, page), "ab") as fh:
            fh.write(b"more\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", BUMP)
        self.commit(NOTE, {"docs/releases/n.md": "note\n"})
        self.assertIn(page, self.refusal(resume_sha=self.head(), allowed_paths=self.STRICT))

    def test_the_rewrite_matches_the_real_step_on_odd_facts_modules(self):
        import subprocess
        with open(os.path.join(ROOT, "scripts", "cut_v1.0.0.sh"), encoding="utf-8") as fh:
            body = fh.read().split("python3 - <<'REPIN'\n", 1)[1].split("\nREPIN\n", 1)[0]
        page = "git clone --branch v0.0.9 url\ncurrently `v0.0.9`, and `--branch v0.0.9`, and v0.0.9 alone\n"
        for facts in ('PUBLIC_INSTALL_TAG = "v0.0.9"\nPUBLIC_INSTALL_TAG = "v0.0.9"\n',
                      'X = 1\nPUBLIC_INSTALL_TAG = "v0.0.9"\nY = "v0.0.9"\n',
                      'PUBLIC_INSTALL_TAG = "v0.0.9"  # a comment\n',
                      'PUBLIC_INSTALL_TAG = "v%s"\n' % RV):
            where = tempfile.mkdtemp(prefix="repin-", dir=self.base)
            files = dict.fromkeys(CR.REPIN_PAGES, page)
            files[CR.REPIN_FACTS] = facts
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(where, rel)), exist_ok=True)
                with open(os.path.join(where, rel), "w", encoding="utf-8") as fh:
                    fh.write(text)
            proc = subprocess.run([sys.executable, "-c", body], cwd=where, env=dict(os.environ, VERSION=RV),
                                  capture_output=True, text=True)
            found = CR._PIN_RE.search(facts)
            for rel, text in files.items():
                with open(os.path.join(where, rel), encoding="utf-8") as fh:
                    left = fh.read()
                if proc.returncode != 0 or found is None or found.group(1) == "v" + RV:
                    self.assertEqual(left, text, (facts, rel, "the step refused or had nothing to move"))
                    self.assertIsNone(found) if proc.returncode != 0 else None
                else:
                    self.assertEqual(left, CR.repin_expected(rel, text, found.group(1), "v" + RV), (facts, rel))

    def check(self, spoil=None, runner=None, leave=None):
        """cut.check_mode at the candidate, its chain replaced by the real step 1b run in the rehearsal tree.
        `leave`: a path the chain modifies and does not commit."""
        self.repin_base()
        seen = {}

        def chain(rehearsal, version, runner=None, stop_early=True, **_kw):
            seen["tree"] = rehearsal
            self.real_step_1b(where=rehearsal, spoil=spoil)
            if leave:
                with open(os.path.join(rehearsal, leave), "a", encoding="utf-8") as fh:
                    fh.write("left behind\n")
            return ["cut_v1.0.0.sh"], []

        out = io.StringIO()
        with mock.patch.object(C, "chain", chain), mock.patch.object(C, "readiness", lambda *a, **k: []), \
                mock.patch.object(C, "_cut_own_paths", lambda root: self.STRICT), contextlib.redirect_stdout(out):
            code = C.check_mode(self.repo, RV, None, (), None, runner=runner)
        self.assertFalse(os.path.exists(seen["tree"]), "the rehearsal tree was left behind")
        return code, out.getvalue()

    def test_the_rehearsal_asks_the_exit_rule_and_reads_ready_on_the_real_step(self):
        code, out = self.check()
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("exit rule: the commits the chain made are the cut's own", out)
        self.assertIn("READY for %s" % RV, out)

    def test_the_rehearsal_is_not_ready_when_the_exit_rule_would_refuse(self):
        code, out = self.check(spoil={CR.REPIN_FACTS: lambda text: text + "EXTRA = 1\n"})
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("products/brothermode/tools changed", out)
        self.assertIn("NOT READY for %s" % RV, out)
        self.assertNotIn("\nREADY for", "\n" + out)

    def test_resume_refuses_a_change_outside_the_cut_s_own_files(self):
        self.record()
        self.cut_chain(note_files={"docs/elsewhere.md": "x\n"})
        head = self.head()
        self.assertIn("docs/elsewhere.md", self.refusal(resume_sha=head, allowed_paths=OWN))
        self.assertNotEqual(self.refusal(resume_sha=head, allowed_paths=()), "")
        self.assertEqual(self.refusal(resume_sha=head, allowed_paths=OWN + ("docs/elsewhere.md",)), "")

    def test_a_record_for_a_different_cut_script_refuses(self):
        self.record(cut_sha="ab" * 32)
        self.assertIn("different scripts/cut.py", self.refusal())

    def test_a_record_with_a_changed_script_tree_refuses(self):
        self.record(trees={"scripts": "1" * 40})
        self.assertIn("scripts changed since", self.refusal())

    def test_an_altered_log_refuses(self):
        path = self.record()
        log = _load(path)["log_path"]
        with open(log, "a") as fh:
            fh.write("edited\n")
        self.assertIn("altered", self.refusal())
        os.remove(log)
        self.assertIn("missing", self.refusal())

    def test_a_stale_record_refuses(self):
        self.record(now=self.now - 23 * 3600)
        self.assertEqual(self.refusal(), "")
        shutil.rmtree(self.ev)
        self.record(now=self.now - 25 * 3600)
        self.assertIn("stale", self.refusal())

    def test_a_record_from_the_future_is_refused(self):
        self.record(now=self.now + 3600)
        self.assertIn("from the future", self.refusal())
        shutil.rmtree(self.ev)
        self.record(now=self.now + 200)
        self.assertEqual(self.refusal(), "")

    def test_a_newer_not_ready_supersedes_an_older_ready(self):
        self.record(now=self.now - 100)
        self.record(verdict="NOT-READY", now=self.now - 50)
        self.assertIn("NOT-READY", self.refusal())
        self.record(now=self.now - 10)
        self.assertEqual(self.refusal(), "")

    def test_a_corrupt_record_refuses(self):
        self.record()
        self.assertEqual(self.refusal(), "")
        with open(os.path.join(self.ev, "rehearsal-%s-aaaaaaaaaaaa-1.json" % RV), "w") as fh:
            fh.write("{not json")
        self.assertIn("corrupt", self.refusal())

    def test_a_missing_folder_a_schema_mismatch_and_a_malformed_record_refuse(self):
        self.assertIn("does not exist", self.refusal())
        path = self.record()
        data = _load(path)
        for change in ({"schema": "cv1.rehearsal.0"}, {"verdict": "MAYBE"}, {"finished_at": "yesterday"},
                       {"script_trees": []}, {"candidate_sha": "abc"}, {"log_path": 5}):
            with open(path, "w") as fh:
                json.dump(dict(data, **change), fh)
            self.assertNotEqual(self.refusal(), "", change)
        del data["cut_py_sha256"]
        with open(path, "w") as fh:
            json.dump(data, fh)
        self.assertIn("missing", self.refusal())

    def test_hostile_input_is_refused_never_a_crash(self):
        self.record()
        self.assertEqual(self.refusal(), "")
        hostile = (None, 5, True, 1.5, float("nan"), [], {}, ["x"], b"1.1.0", object())
        for value in hostile:
            for field in ("version", "resume_sha", "directory", "now", "allowed_paths"):
                kwargs = dict(version=RV, resume_sha="", directory=self.ev, now=self.now, allowed_paths=())
                kwargs[field] = value
                if value is None and field in ("resume_sha", "now"):
                    continue
                if field == "allowed_paths" and isinstance(value, list) and all(isinstance(v, str) for v in value):
                    continue
                got = CR.rehearsal_refusal(self.repo, kwargs["version"], kwargs["resume_sha"], kwargs["directory"],
                                           None, kwargs["now"], kwargs["allowed_paths"])
                self.assertIsInstance(got, str)
                self.assertNotEqual(got, "", (field, value))
            self.assertNotEqual(CR.rehearsal_refusal(value, RV, directory=self.ev, now=self.now), "", value)
            self.assertNotEqual(CR.resume_chain_problem(value, "a" * 40, "b" * 40, RV, ()), "")
            self.assertNotEqual(CR.resume_chain_problem(self.repo, value, "b" * 40, RV, ()), "")
            self.assertNotEqual(CR.resume_chain_problem(self.repo, "a" * 40, "b" * 40, RV, value), "")
            with self.assertRaises(ValueError):
                CR.run_rehearsal(self.repo, value)
            with self.assertRaises(ValueError):
                CR.record_rehearsal(value, "a" * 40, "b" * 40, 0, "NO-DATA", "", 1.0, "c" * 64)
            with self.assertRaises(ValueError):
                CR.record_rehearsal(RV, value, "b" * 40, 0, "NO-DATA", "", 1.0, "c" * 64)
            with self.assertRaises(ValueError):
                CR.record_rehearsal(RV, "a" * 40, "b" * 40, 0, value, "", 1.0, "c" * 64)
        for bad_exit in (True, None, "0", 1.5):
            with self.assertRaises(ValueError):
                CR.record_rehearsal(RV, "a" * 40, "b" * 40, bad_exit, "NO-DATA", "", 1.0, "c" * 64)
        for bad_seconds in (True, None, float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                CR.record_rehearsal(RV, "a" * 40, "b" * 40, 0, "NO-DATA", "", bad_seconds, "c" * 64)
        with self.assertRaises(ValueError):
            CR.run_rehearsal(self.repo, RV, timeout_s=True)
        with self.assertRaises(ValueError):
            CR.record_rehearsal(RV, "a" * 40, "b" * 40, 0, "READY", "", 1.0, "c" * 64)

    def test_the_evidence_folder_is_redirected_only_by_the_fixture_seam(self):
        self.assertEqual(CR.evidence_dir(), os.path.abspath(self.ev))
        env = dict((k, v) for k, v in os.environ.items() if k != "BROTHER_CUT_TEST")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(CR.evidence_dir(), os.path.join(os.environ["HOME"], ".claude", "evidence", "cut"))
        self.assertEqual(CR.scratch_root(), os.path.abspath(self.bs))


def _catalog(names, indent=2, extra=None):
    plugins = []
    for name in names:
        plugins.append({"name": name, "source": {"source": "git-subdir", "path": "bundle", "ref": "v1.0.0"},
                        "version": "1.0.0"})
    doc = {"name": "brother", "metadata": {"version": "1.0.0"}, "plugins": plugins}
    doc.update(extra or {})
    return json.dumps(doc, indent=indent) + "\n"


class CV1cRetireCatalogs(unittest.TestCase):
    """CV1.c: the U8 catalog step and the pre cut refusal when catalogs are not in end state."""

    CLAUDE = os.path.join(".claude-plugin", "marketplace.json")
    CURSOR = os.path.join(".cursor-plugin", "marketplace.json")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cv1c-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.write(self.CLAUDE, _catalog(["brothermode", "brother", "brotherds"], indent=4))
        cursor = json.loads(_catalog(["brother", "brothersbe"]))
        for p in cursor["plugins"]:
            p["source"] = "bundle"
        self.write(self.CURSOR, json.dumps(cursor, indent=2) + "\n")
        self.write(RC.AGENTS, json.dumps({"name": "brother", "plugins": [{"name": "brother"}]}, indent=2) + "\n")
        for rel in RC.DELETE:
            self.write(rel, _catalog(["x"]))

    def write(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.tmp, rel), encoding="utf-8") as fh:
            return fh.read()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = RC.main(["--root", self.tmp] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_applies_is_true_only_from_the_floor_upward(self):
        for version, want in (("1.0.99", False), ("0.9.9", False), ("1.1.0", True), ("1.1.1", True),
                              ("1.2.0", True), ("2.0.0", True), ("10.0.0", True)):
            self.assertIs(RC.applies(version), want, version)

    def test_hostile_versions_are_refused_never_a_crash(self):
        for bad in (None, True, 1, 1.1, float("nan"), "", "1.1", "1.1.0 ", "v1.1.0", "1.1.0\n", ["1.1.0"],
                    {"v": 1}, b"1.1.0", "01.1.0"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                RC.applies(bad)
            if bad is not None:  # None is the legacy bare apply, which writes no version
                with self.assertRaises(ValueError, msg=repr(bad)):
                    RC.apply(self.tmp, bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                RC.problems(self.tmp, bad)
        code, _, err = self.run_main("--apply", "--version", "banana")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", err)
        self.assertEqual(self.run_main("--check", "--version", "1.1")[0], 2)

    def test_apply_writes_the_version_and_ref_and_reaches_the_end_state(self):
        code, out, _ = self.run_main("--apply", "--version", "1.1.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("retire_catalogs:")]), 4, out)
        doc = json.loads(self.read(self.CLAUDE))
        self.assertEqual([p["name"] for p in doc["plugins"]], ["brother"])
        self.assertEqual(doc["plugins"][0]["version"], "1.1.0")
        self.assertEqual(doc["plugins"][0]["source"]["ref"], "v1.1.0")
        cursor = json.loads(self.read(self.CURSOR))
        self.assertEqual([p["name"] for p in cursor["plugins"]], ["brother"])
        self.assertEqual(cursor["plugins"][0]["version"], "1.1.0")
        self.assertEqual(cursor["plugins"][0]["source"], "bundle")
        for rel in RC.DELETE:
            self.assertFalse(os.path.exists(os.path.join(self.tmp, rel)))
        # The chain's check now reads the WHOLE end state through the one reader (OP1.e,
        # donecheck_u8.catalog_problems), so on this catalogs only fixture it is NOT DONE until the parts other
        # chain steps own exist: the bundle's version source and manifest, the agents entry's source, and the
        # catalog's own metadata version (the version bump writes that one).
        code, out, _ = self.run_main("--check", "--version", "1.1.0")
        self.assertEqual(code, 1, out)
        self.assertIn("bundle", out)
        self.write(os.path.join("bundle", ".claude-plugin", "plugin.json"), json.dumps({"name": "brother", "version": "1.1.0"}))
        self.write(os.path.join("bundle", "MANIFEST.json"), json.dumps({"shipped_plugins": ["brother"], "entries": {}, "total": 0}))
        self.write(RC.AGENTS, json.dumps({"name": "brother", "plugins": [
            {"name": "brother", "source": {"source": "local", "path": "./bundle"}}]}, indent=2) + "\n")
        doc["metadata"]["version"] = "1.1.0"
        self.write(self.CLAUDE, json.dumps(doc, indent=4) + "\n")
        self.assertEqual(self.run_main("--check", "--version", "1.1.0")[0], 0)

    def test_apply_keeps_indentation_and_key_order(self):
        self.run_main("--apply", "--version", "1.1.0")
        text = self.read(self.CLAUDE)
        self.assertTrue(text.startswith('{\n    "name"'), text[:20])
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(list(json.loads(text)), ["name", "metadata", "plugins"])

    def test_a_second_apply_says_so_and_writes_nothing(self):
        self.run_main("--apply", "--version", "1.1.0")
        before = (self.read(self.CLAUDE), self.read(self.CURSOR), os.stat(os.path.join(self.tmp, self.CLAUDE)).st_mtime_ns)
        code, out, _ = self.run_main("--apply", "--version", "1.1.0")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "retire_catalogs: already in end state")
        self.assertEqual(before, (self.read(self.CLAUDE), self.read(self.CURSOR),
                                  os.stat(os.path.join(self.tmp, self.CLAUDE)).st_mtime_ns))

    def test_below_the_floor_both_modes_are_not_applicable_and_touch_nothing(self):
        files = (self.CLAUDE, self.CURSOR) + RC.DELETE
        before = [self.read(r) for r in files]
        for mode in ("--apply", "--check"):
            code, out, _ = self.run_main(mode, "--version", "1.0.99")
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "retire_catalogs: NOT APPLICABLE below 1.1.0")
        self.assertEqual(before, [self.read(r) for r in files])

    def test_check_prints_one_line_per_problem_and_exits_1(self):
        code, out, _ = self.run_main("--check", "--version", "1.1.0")
        self.assertEqual(code, 1)
        lines = [l for l in out.splitlines() if "NOT DONE" in l]
        self.assertTrue(any(self.CLAUDE in l and "brothermode" in l for l in lines), out)
        self.assertTrue(any(self.CURSOR in l for l in lines), out)
        self.assertEqual(len([l for l in lines if "still exists" in l]), 2, out)

    def test_check_refuses_another_version_or_ref(self):
        self.run_main("--apply", "--version", "1.1.0")
        code, out, _ = self.run_main("--check", "--version", "1.1.1")
        self.assertEqual(code, 1)
        self.assertIn("version is '1.1.0', not 1.1.1", out)
        self.assertIn("source.ref is 'v1.1.0', not v1.1.1", out)

    def test_an_agents_catalog_with_another_plugin_fails_the_check(self):
        self.run_main("--apply", "--version", "1.1.0")
        self.write(RC.AGENTS, json.dumps({"plugins": [{"name": "brother"}, {"name": "other"}]}))
        code, out, _ = self.run_main("--check", "--version", "1.1.0")
        self.assertEqual(code, 1)
        self.assertIn(RC.AGENTS, out)

    def test_unreadable_catalogs_are_no_data_never_a_pass(self):
        dup = '{"name": "brother", "name": "x", "plugins": [{"name": "brother"}]}'
        cases = {
            "duplicate key": dup,
            "invalid json": "{nope",
            "no brother": json.dumps({"plugins": [{"name": "brothermode"}]}),
            "nameless entry": json.dumps({"plugins": [{"name": "brother"}, {"source": "x"}]}),
            "non object entry": json.dumps({"plugins": ["brother"]}),
            "two brothers": json.dumps({"plugins": [{"name": "brother"}, {"name": "brother"}]}),
            "no plugins list": json.dumps({"plugins": "brother"}),
        }
        for label, text in cases.items():
            for rel in (self.CLAUDE, self.CURSOR, RC.AGENTS):
                self.setUp()
                self.write(rel, text)
                for mode in ("--check", "--apply"):
                    code, _, err = self.run_main(mode, "--version", "1.1.0")
                    self.assertEqual(code, 2, (label, rel, mode, err))
                    self.assertIn("NO-DATA", err)

    def test_a_duplicate_key_is_refused_by_the_bare_check_too(self):
        self.write(self.CLAUDE, '{"plugins": [{"name": "brother", "name": "brothermode"}]}')
        self.assertEqual(self.run_main("--check")[0], 2)

    def test_staged_writes_leave_every_catalog_untouched_when_a_later_one_is_bad(self):
        before = self.read(self.CLAUDE)
        self.write(self.CURSOR, "{nope")
        code, _, _ = self.run_main("--apply", "--version", "1.1.0")
        self.assertEqual(code, 2)
        self.assertEqual(self.read(self.CLAUDE), before)
        for rel in RC.DELETE:
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)

    def test_no_staging_file_is_left_behind(self):
        self.run_main("--apply", "--version", "1.1.0")
        left = [f for d, _, fs in os.walk(self.tmp) for f in fs if f.endswith(".retire-tmp")]
        self.assertEqual(left, [])

    def test_apply_and_version_flags_are_validated(self):
        for args in (["--apply"], ["--version", "1.1.0"], ["--apply", "--check", "--version", "1.1.0"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    RC.main(["--root", self.tmp] + args)
            self.assertEqual(ctx.exception.code, 2, args)

    def test_bare_apply_and_bare_check_keep_their_behaviour(self):
        code, out, _ = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("retire_catalogs: NOT DONE: "), out)
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("retire_catalogs: changed "), out)
        self.assertEqual(json.loads(self.read(self.CLAUDE))["plugins"][0]["version"], "1.0.0")
        self.assertEqual(self.run_main("--check")[1].strip(), "retire_catalogs: DONE: every catalog lists only brother")

    def chain_calls(self, **kw):
        calls = []
        outputs = kw.pop("outputs", {})

        def runner(cmd, **_kw):
            name = os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0]
            calls.append((name, list(cmd)))
            code, out = outputs.get(name, (0, "ok\n"))
            return mock.Mock(returncode=code, stdout=out, stderr="")

        with contextlib.redirect_stdout(io.StringIO()):
            result = C.chain(self.tmp, "1.1.0", runner, **kw)
        return calls, result

    def test_the_chain_applies_before_the_cut_script_and_checks_after_donecheck(self):
        calls, (passed, failed) = self.chain_calls()
        names = [n for n, _ in calls]
        self.assertEqual(failed, [])
        self.assertLess(names.index("retire_catalogs.py"), names.index("cut_v1.0.0.sh"))
        apply_cmd = calls[names.index("retire_catalogs.py")][1]
        self.assertEqual(apply_cmd[2:], ["--apply", "--version", "1.1.0"])
        last = len(names) - 1 - names[::-1].index("retire_catalogs.py")
        self.assertGreater(last, names.index("donecheck_u8.py"))
        self.assertEqual(calls[last][1][2:], ["--check", "--version", "1.1.0"])
        self.assertIn("retire_catalogs --apply", passed)
        self.assertEqual(passed[-1], "retire_catalogs --check")

    def test_a_refused_check_stops_the_chain_and_a_refused_apply_stops_before_the_cut(self):
        calls, (passed, failed) = self.chain_calls(outputs={"retire_catalogs.py": (1, "NOT DONE\n")})
        self.assertEqual(failed, ["retire_catalogs --apply"])
        self.assertNotIn("cut_v1.0.0.sh", [n for n, _ in calls])
        calls, (passed, failed) = self.chain_calls(resume=True, outputs={"retire_catalogs.py": (1, "NOT DONE\n")})
        self.assertEqual(failed, ["retire_catalogs --check"])

    def test_resume_skips_the_apply_and_still_checks(self):
        calls, (passed, failed) = self.chain_calls(resume=True)
        self.assertEqual(failed, [])
        steps = [c[1][2] for c in calls if c[0] == "retire_catalogs.py"]
        self.assertEqual(steps, ["--check"])
        self.assertNotIn("retire_catalogs --apply", passed)

    def test_not_applicable_reads_n_a_never_a_pass(self):
        out = {"retire_catalogs.py": (0, "retire_catalogs: NOT APPLICABLE below 1.1.0\n")}
        buf = io.StringIO()
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            code, text = out.get(os.path.basename(cmd[1]) if len(cmd) > 1 else "", (0, "ok\n"))
            return mock.Mock(returncode=code, stdout=text, stderr="")

        with contextlib.redirect_stdout(buf):
            passed, failed = C.chain(self.tmp, "1.1.0", runner)
        self.assertEqual(failed, [])
        self.assertNotIn("retire_catalogs --apply", passed)
        self.assertNotIn("retire_catalogs --check", passed)
        self.assertIn("retire_catalogs --apply: n/a", buf.getvalue())

    def test_the_fence_covers_every_catalog_the_step_edits(self):
        paths = C.release_paths(self.tmp)
        self.assertEqual(paths[:4], ["scripts/", "bundle/", "docs/releases/", ".claude-plugin/"])
        self.assertIn(".cursor-plugin/", paths)
        self.assertIn(".agents/plugins/", paths)

    def test_the_surface_assertion_accepts_exactly_two_shapes(self):
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        try:
            import test_surface as TS
        finally:
            sys.path.pop(0)
        ok = TS.plugins_shape_ok
        b, o = {"name": "brother"}, {"name": "other"}
        self.assertTrue(ok([b]))
        self.assertTrue(ok([o, b, {"name": "c"}]))
        self.assertTrue(ok([b, o]))
        for bad in ([], [o], [o, {"name": "c"}], None, "brother", ["brother"], [None], [{"name": None}]):
            self.assertFalse(ok(bad), repr(bad))


class CV1dReleasePlan(unittest.TestCase):
    """CV1.d: a release plan row stating 'N of M units DONE' must match the launch board, and the preflight maps the
    release plan check's exit code to one row. Planted plan and board only: CV1.e and CV1.f extend this file, so a test
    here that read the real board would turn red at every unit closure for a reason outside their build (review
    2026-10-04). The real tree's currency is asserted by scripts/test_release_plan_live.py and, at cut time, by the
    preflight's release-plan row, which the rehearsal runs."""

    @classmethod
    def setUpClass(cls):
        import gen_release_plan as G
        import cut_preflight as P
        cls.G, cls.P = G, P

    BOARD = {"units": [{"id": "A", "state": "DONE"}, {"id": "B", "state": "DONE"}, {"id": "C", "state": "OPEN"}]}

    def _plan(self, evidence):
        return {"cut_requirements": [{"req": "Every board row DONE", "evidence": evidence}, {"req": "other", "evidence": "no count"}]}

    def test_a_row_that_matches_the_board_is_clean(self):
        self.assertEqual(self.G.stale_counts(self._plan("launch WBS: 2 of 3 units DONE"), self.BOARD), [])

    def test_a_row_set_to_one_of_two_is_named_once(self):
        got = self.G.stale_counts(self._plan("launch WBS: 1 of 2 units DONE"), self.BOARD)
        self.assertEqual(len(got), 1, got)
        self.assertIn("1 of 2 units DONE", got[0])
        self.assertIn("the board reads 2 of 3", got[0])

    def test_either_number_alone_differing_is_named(self):
        self.assertEqual(len(self.G.stale_counts(self._plan("2 of 4 units DONE"), self.BOARD)), 1)
        self.assertEqual(len(self.G.stale_counts(self._plan("1 of 3 units DONE"), self.BOARD)), 1)

    def test_evidence_that_is_not_text_is_reported_never_skipped(self):
        got = self.G.stale_counts({"cut_requirements": [{"req": "r", "evidence": 7}]}, {"units": []})
        self.assertEqual(len(got), 1, got)
        self.assertIn("not text", got[0])

    def test_hostile_shapes_answer_a_sentence_never_a_raise(self):
        for plan, launch in (({}, {}), (None, None), ({"cut_requirements": "x"}, {"units": []}),
                             ({"cut_requirements": [None, 3]}, {"units": [None]})):
            self.assertIsInstance(self.G.stale_counts(plan, launch), list)

    def test_coverage_problems_carries_the_stale_count(self):
        plan = {"stages": [{"id": "S%d" % i} for i in range(1, 8)], "workstreams": [],
                "cut_requirements": [{"req": "extra", "evidence": "1 of 2 units DONE"}]}
        board = {"units": [{"id": "A", "state": "DONE"}]}
        self.assertTrue(any("1 of 2 units DONE" in p for p in self.G.coverage_problems(plan, board, {"units": []})))

    def _row(self, code, out=""):
        import subprocess
        def runner(cmd, **kw):
            return subprocess.CompletedProcess(cmd, code, out, "")
        return self.P.check_release_plan(ROOT, runner)

    def test_the_preflight_row_maps_each_exit_code(self):
        self.assertEqual(self._row(0)[:2], (self.P.OK, "release-plan"))
        refused = self._row(1, "FAIL: row says 1 of 2 units DONE")
        self.assertEqual(refused[0], self.P.REFUSED)
        self.assertIn("FAIL: row says 1 of 2", refused[2])
        for code in (2, 124, 127, 3):
            self.assertEqual(self._row(code)[0], self.P.NODATA, code)

    def test_a_crash_without_the_fail_line_is_no_data_never_refused(self):
        self.assertEqual(self._row(1, "Traceback (most recent call last):\nKeyError: 'units'")[0], self.P.NODATA)

    def test_a_missing_script_or_root_is_no_data(self):
        box = tempfile.mkdtemp(prefix="cv1d-")
        self.addCleanup(shutil.rmtree, box, True)
        self.assertEqual(self.P.check_release_plan(box)[0], self.P.NODATA)
        for root in (None, "", 0, ["x"]):
            self.assertEqual(self.P.check_release_plan(root)[0], self.P.NODATA)

    def test_run_all_places_the_row_right_after_the_readiness_rows(self):
        import inspect
        src = inspect.getsource(self.P.run_all)
        self.assertLess(src.index("check_readiness_rows(root, at, runner)"), src.index("check_release_plan(root, runner)"))
        self.assertLess(src.index("check_release_plan(root, runner)"), src.index("check_changelog_text("))


SEAM_HEAD = "a" * 40
MOVED_HEAD = "b" * 40
SEAM_BM = "/fake/bm_store.py"
SEAM_TAGGED = "c" * 40
#: A fixed clock for every stop record a test writes itself.
SEAM_NOW = 1790000000.0


def _seam_label(cmd):
    """One label per command shape: git and gh by their verbs, bm_store by
    its subcommand, a script by its name plus its first flag."""
    if cmd[0] == "git":
        return "git " + " ".join(cmd[1:3])
    if cmd[0] == "sh":
        return os.path.basename(cmd[1])
    if cmd[0] == "gh":
        return "gh " + " ".join(cmd[1:3])
    if len(cmd) > 2 and cmd[1] == SEAM_BM:
        return "bm_store " + cmd[2]
    name = os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0]
    if len(cmd) > 2 and cmd[2].startswith("--"):
        return "%s %s" % (name, cmd[2])
    return name


class SeamRunner(object):
    """Answers every command of a whole cut from `answers` (label -> (exit,
    stdout)), default exit 0 with no output, and records every label. Never
    the network, never a real push: export_public.py is one more label."""

    def __init__(self, **answers):
        self.answers = {
            "git rev-parse HEAD": (0, SEAM_HEAD + "\n"),
            "git status --porcelain": (0, ""),
            "bm_store dump": (0, json.dumps({"records": [], "claims": []})),
            "bm_store claim": (0, "claimed 'release-cut-%s' as lifecycle %s "
                                  "(version 1, session s)\n" % (RV, "d" * 32)),
            "retire_catalogs.py --check": (0, "retire_catalogs: DONE\n"),
            "export_public.py --push": (
                0, "TAGGED: v%s points at the merged tip (local commit %s) "
                   "on main\n" % (RV, SEAM_TAGGED)),
        }
        self.answers.update(answers)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        label = _seam_label(list(cmd))
        self.calls.append(label)
        code, out = self.answers.get(label, (0, ""))
        return Done(cmd, code, out)

    def count(self, label):
        return self.calls.count(label)


class CV1eAnswerSeam(unittest.TestCase):
    """CV1.e: the owner's terminal is the only seam. Every test drives the
    REAL cut.main, cut.run_chain or cut.answer_file_refusal with a scripted
    runner, a fixed clock for the records it writes, and a throwaway evidence
    folder; the covering rehearsal record (proven by CV1bRehearsal) is
    answered by a fake that counts its calls."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="cv1e-")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.root = os.path.join(self.scratch, "tree")
        self.ev = os.path.join(self.scratch, "evidence")
        os.makedirs(self.root)
        self.record = os.path.join(self.ev, "rehearsal-%s-aaaaaaaaaaaa-1.json"
                                   % RV)
        self.cover_reason = ""
        self.cover_calls = []
        self.asked = []
        self.answer = "y"
        self.on_ask = None
        env = dict((k, v) for k, v in os.environ.items()
                   if k not in C.CUT_REDIRECT_ENV)
        for patcher in (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(CR, "covering_record", self._cover),
                mock.patch.object(CR, "evidence_dir", return_value=self.ev)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cover(self, root, version, resume_sha="", directory="", runner=None,
               now=None, allowed_paths=()):
        self.cover_calls.append((version, resume_sha))
        if self.cover_reason:
            return "", self.cover_reason
        return self.record, ""

    def ask(self, prompt):
        self.asked.append(prompt)
        if self.on_ask:
            self.on_ask()
        return self.answer

    def chain(self, runner, yes=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, evidence, note = C.run_chain(
                self.root, RV, (None, None, "a test"), ["scripts/"], None,
                yes, self.ask, runner)
        return code, note, out.getvalue()

    def main(self, argv, runner, tty=True, gates=True, root=None):
        out = io.StringIO()
        del C._BM_STORE_CACHE[:]
        patches = [mock.patch.object(C, "_stdin_is_tty", return_value=tty),
                   mock.patch.object(C, "_load_bm_store",
                                     return_value=(FakeBm, SEAM_BM, None)),
                   mock.patch.object(C, "release_paths",
                                     return_value=["scripts/"])]
        if gates:
            patches += [mock.patch.object(C, "version_skip_refusal",
                                          return_value=""),
                        mock.patch.object(C, "rehearsal_refusal",
                                          return_value="")]
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(contextlib.redirect_stdout(out))
            code = C.main(["--version", RV, "--no-release-page", "a test"]
                          + argv, root=root or self.root, runner=runner,
                          ask=self.ask)
        del C._BM_STORE_CACHE[:]
        return code, out.getvalue()

    def stops(self):
        if not os.path.isdir(self.ev):
            return []
        return sorted(n for n in os.listdir(self.ev) if n.startswith("stop-"))

    def stop_data(self):
        names = self.stops()
        self.assertEqual(len(names), 1, names)
        with open(os.path.join(self.ev, names[0]), encoding="utf-8") as fh:
            return os.path.join(self.ev, names[0]), json.load(fh)

    # --- controls 1 and 2: --yes and the answer need the owner's terminal -----

    def test_yes_without_a_terminal_is_refused_before_any_step(self):
        runner = SeamRunner()
        code, out = self.main(["--yes"], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertTrue(out.startswith("NO-DATA: --yes needs a person at a "
                                       "terminal"), out)
        self.assertEqual(runner.calls, [])
        # a person at a terminal may still pass --yes: it reaches the fence
        runner = SeamRunner()
        code, out = self.main(["--yes"], runner, tty=True)
        self.assertEqual(runner.count("bm_store claim"), 1, out)
        self.assertEqual(self.asked, [])
        for tty in (False, None, 1, "yes"):
            self.assertTrue(C.yes_refusal(True, tty).startswith("NO-DATA"),
                            tty)
        self.assertEqual(C.yes_refusal(False, False), "")
        self.assertEqual(C.yes_refusal(True, True), "")

    def test_a_real_cut_refuses_an_answer_file(self):
        path = os.path.join(self.scratch, "answer")
        runner = SeamRunner()
        code, out = self.main(["--answer-file", path], runner, tty=True)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("the answer is typed on the owner's terminal", out)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.asked, [])
        self.assertFalse(os.path.exists(path))
        self.assertTrue(C.answer_file_refusal(path, False, True)
                        .startswith("NO-DATA"))
        # only a True dry run is a dry run
        for dry in (False, None, 1, "yes"):
            self.assertTrue(C.answer_file_refusal(path, dry, True)
                            .startswith("NO-DATA"), dry)
        self.assertEqual(C.answer_file_refusal(path, True, False), "")

    def test_a_real_cut_without_a_terminal_refuses_before_the_question(self):
        runner = SeamRunner()
        code, out = self.main([], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("reads its one answer from a terminal", out)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.asked, [])
        self.assertTrue(C.answer_file_refusal(None, False, False)
                        .startswith("NO-DATA"))
        self.assertEqual(C.answer_file_refusal(None, False, True), "")

    def test_the_rehearsal_s_check_without_a_terminal_is_not_refused(self):
        # cut_rehearsal runs `cut.py --check --version V` with no terminal
        for argv in (["--check"],
                     ["--check", "--answer-file",
                      os.path.join(self.scratch, "unused")]):
            runner = SeamRunner()
            code, out = self.main(argv, runner, tty=False)
            self.assertNotIn("terminal", out, argv)
            self.assertIn("cut --check: %s" % RV, out)
            self.assertIn("retire_catalogs.py --check", runner.calls)
            self.assertEqual(runner.count("bm_store claim"), 0)
            self.assertEqual(self.asked, [])
        self.assertEqual(C.answer_file_refusal(None, True, False), "")

    def test_only_y_or_yes_approves(self):
        for answer, approved in (("y", True), ("Y", True), ("yes", True),
                                 (" YES \n", True), ("", False), ("n", False),
                                 ("yep", False), ("yes please", False),
                                 ("yy", False), ("y es", False),
                                 ("ye", False)):
            self.answer = answer
            runner = SeamRunner()
            code, note, out = self.chain(runner)
            self.assertEqual("export_public.py --push" in runner.calls,
                             approved, (answer, out))
            if not approved:
                self.assertEqual(code, C.EXIT_OK)
                self.assertIn("declined, nothing pushed", out)

    # --- control 3: the question names what is approved -----------------------

    def test_the_question_names_version_sha_and_rehearsal(self):
        text = C.question_text(RV, SEAM_HEAD, self.record, "CLAUSE")
        for part in (RV, SEAM_HEAD, self.record, "CLAUSE"):
            self.assertIn(part, text)
        self.assertTrue(text.rstrip().endswith("[y/N]"), text)
        self.answer = "n"
        runner = SeamRunner()
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertEqual(len(self.asked), 1)
        prompt = self.asked[0]
        for part in ("v%s" % RV, SEAM_HEAD, self.record,
                     "no GitHub Release will be published"):
            self.assertIn(part, prompt)
        self.assertTrue(prompt.rstrip().endswith("[y/N]"), prompt)
        # covering_record asked once, after the chain, for HEAD then
        self.assertEqual(self.cover_calls, [(RV, SEAM_HEAD)])

    def test_a_record_gone_before_the_question_stops_the_cut(self):
        self.cover_reason = "REFUSED: no rehearsal record covers HEAD aaaa"
        runner = SeamRunner()
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("refused before the question", note)
        self.assertIn(self.cover_reason, note)
        # 2026-10-07: the reason went into the fence note only, and the owner's terminal showed a bare prompt
        self.assertIn("refused before the question, nothing pushed: %s" % self.cover_reason, out)
        self.assertEqual(self.asked, [])
        self.assertNotIn("export_public.py --push", runner.calls)
        runner = SeamRunner(**{"git rev-parse HEAD": (0, "not a sha\n")})
        self.cover_reason = ""
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("HEAD cannot be read", note)
        self.assertEqual(self.asked, [])

    # --- control 6: nothing moves between the answer and the push ------------

    def test_a_green_push_completes_after_the_recheck(self):
        runner = SeamRunner()
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("export_public.py --push", runner.calls)
        self.assertEqual(runner.count("retire_catalogs.py --check"), 2)
        self.assertEqual(len(self.cover_calls), 2)
        self.assertEqual(self.stops(), [])

    def test_a_commit_during_the_answer_wait_refuses_the_push(self):
        runner = SeamRunner()
        self.on_ask = lambda: runner.answers.update(
            {"git rev-parse HEAD": (0, MOVED_HEAD + "\n")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("nothing pushed", note)
        self.assertIn("HEAD is %s, not %s" % (MOVED_HEAD, SEAM_HEAD), note)
        self.assertNotIn("export_public.py --push", runner.calls)
        self.assertEqual(self.stops(), [])

    def test_an_uncommitted_edit_during_the_answer_wait_refuses_the_push(self):
        runner = SeamRunner()
        self.on_ask = lambda: runner.answers.update(
            {"git status --porcelain": (0, " M scripts/export_public.py\n")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("scripts/export_public.py", note)
        self.assertNotIn("export_public.py --push", runner.calls)
        # unreadable status refuses too
        runner = SeamRunner()
        self.on_ask = lambda: runner.answers.update(
            {"git status --porcelain": (128, "")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertNotIn("export_public.py --push", runner.calls)

    def test_pre_push_recheck_refuses_a_catalog_changed_after_the_answer(self):
        runner = SeamRunner()
        self.on_ask = lambda: runner.answers.update(
            {"retire_catalogs.py --check":
             (1, "retire_catalogs: NOT DONE: cursor catalog lists brothersbe")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("retire_catalogs --check exited 1", note)
        self.assertEqual(runner.count("retire_catalogs.py --check"), 2)
        self.assertNotIn("export_public.py --push", runner.calls)

    def test_pre_push_recheck_refuses_a_record_that_no_longer_covers(self):
        runner = SeamRunner()
        self.assertEqual(C.pre_push_recheck(self.root, RV, SEAM_HEAD,
                                            self.record, runner=runner), "")
        why = C.pre_push_recheck(self.root, RV, SEAM_HEAD, "/another/record",
                                 runner=runner)
        self.assertTrue(why.startswith("REFUSED"), why)
        for head, record in (("", self.record), (None, self.record),
                             (SEAM_HEAD[:12], self.record),
                             (SEAM_HEAD, ""), (SEAM_HEAD, None)):
            why = C.pre_push_recheck(self.root, RV, head, record,
                                     runner=runner)
            self.assertTrue(why.startswith("REFUSED"), (head, record, why))
        self.cover_reason = "REFUSED: the log was altered"
        why = C.pre_push_recheck(self.root, RV, SEAM_HEAD, self.record,
                                 runner=runner)
        self.assertIn("the log was altered", why)

    # --- controls 4 and 5: a failed push is a recorded stop -------------------

    def test_a_refused_push_files_a_stop_record(self):
        runner = SeamRunner(**{"export_public.py --push":
                               (1, "remote: Blocked by the classifier\n"
                                   + "x" * 1000)})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        path, data = self.stop_data()
        self.assertEqual(os.path.basename(path),
                         "stop-%s-%s.json" % (RV, SEAM_HEAD[:12]))
        self.assertEqual(data["kind"], "host-refused")
        self.assertEqual(data["sha"], SEAM_HEAD)
        self.assertEqual(data["version"], RV)
        self.assertLessEqual(len(data["detail"]), 400)
        self.assertIn("Blocked", data["detail"])
        self.assertTrue(note.startswith("STOP:"), note)
        self.assertIn(path, note)
        self.assertIn("not retried or routed around", note)
        self.assertEqual(runner.count("export_public.py --push"), 1)
        self.assertNotIn("reproduce_export.py", runner.calls)

    def test_a_host_refusal_blocks_the_same_cut_again(self):
        runner = SeamRunner(**{"export_public.py --push":
                               (1, "! [remote rejected] main (protected branch "
                                   "hook declined)\n")})
        self.chain(runner)
        path, data = self.stop_data()
        self.assertEqual(data["kind"], "host-refused")
        del self.asked[:]
        runner = SeamRunner()
        code, out = self.main([], runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn(path, out)
        self.assertEqual(runner.count("bm_store claim"), 0)
        self.assertEqual(self.asked, [])
        # cleared only by the owner deleting the file
        os.remove(path)
        self.assertEqual(C.stop_record_refusal(self.ev, RV), "")
        runner = SeamRunner()
        self.answer = "n"
        self.main([], runner)
        self.assertEqual(runner.count("bm_store claim"), 1)

    def test_a_host_refusal_still_blocks_after_head_moves(self):
        path = C.write_stop_record(self.ev, RV, SEAM_HEAD, "host-refused",
                                   "Blocked", now=SEAM_NOW)
        runner = SeamRunner(**{"git rev-parse HEAD": (0, MOVED_HEAD + "\n")})
        code, out = self.main([], runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn(path, out)
        self.assertEqual(runner.count("bm_store claim"), 0)
        # another version is not blocked by it
        self.assertEqual(C.stop_record_refusal(self.ev, "9.9.9"), "")

    def test_a_corrupt_stop_record_refuses(self):
        os.makedirs(self.ev)
        bad = os.path.join(self.ev, "stop-%s-%s.json" % (RV, "f" * 12))
        for text in ("{", "[]", json.dumps({"schema": "cv1.stop.1",
                                            "version": RV, "kind": "other"})):
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write(text)
            why = C.stop_record_refusal(self.ev, RV)
            self.assertTrue(why.startswith("REFUSED"), (text, why))
            self.assertIn(bad, why)
        for value in (None, "", 7):
            self.assertTrue(C.stop_record_refusal(value, RV)
                            .startswith("REFUSED"), value)
        self.assertTrue(C.stop_record_refusal(self.ev, "1.1")
                        .startswith("NO-DATA"))
        for args in (("1.1", SEAM_HEAD, "host-refused", ""),
                     (RV, SEAM_HEAD[:12], "host-refused", ""),
                     (RV, SEAM_HEAD, "other", ""),
                     (RV, SEAM_HEAD, "host-refused", None)):
            with self.assertRaises(ValueError):
                C.write_stop_record(self.ev, *args, now=SEAM_NOW)

    def test_a_refusal_carrying_a_network_phrase_is_a_host_refusal(self):
        for text in ("error: 403 Forbidden; Connection reset by peer",
                     "Blocked by the classifier after the request timed out",
                     "remote: Permission denied. fatal: early EOF",
                     "protected branch: RPC failed"):
            self.assertEqual(C.classify_push_failure(text), "host-refused",
                             text)
        self.assertEqual(C.classify_push_failure(
            "fatal: unable to access: Could not resolve host: github.com"),
            "push-failed")

    def test_an_unrecognised_failure_is_a_host_refusal(self):
        for text in ("", None, b"Could not resolve host", 7,
                     "fatal: something nobody has seen before"):
            self.assertEqual(C.classify_push_failure(text), "host-refused",
                             text)
        runner = SeamRunner(**{"export_public.py --push": (1, "")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(self.stop_data()[1]["kind"], "host-refused")
        self.assertTrue(C.stop_record_refusal(self.ev, RV)
                        .startswith("REFUSED"))

    def test_a_network_failure_does_not_block_a_retry(self):
        runner = SeamRunner(**{"export_public.py --push":
                               (128, "fatal: unable to access 'https://github"
                                     ".com/x/y.git/': Could not resolve host: "
                                     "github.com\n")})
        code, note, out = self.chain(runner)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(self.stop_data()[1]["kind"], "push-failed")
        self.assertIn("network failure", note)
        self.assertEqual(C.stop_record_refusal(self.ev, RV), "")
        runner = SeamRunner()
        self.answer = "n"
        code, out = self.main([], runner)
        self.assertEqual(runner.count("bm_store claim"), 1, out)

    def test_an_unwritable_evidence_folder_refuses_the_real_cut_before_the_question(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root writes into a read only folder")
        os.makedirs(self.ev)
        os.chmod(self.ev, 0o500)
        self.addCleanup(os.chmod, self.ev, 0o700)
        runner = SeamRunner()
        code, out = self.main([], runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("is not writable", out)
        self.assertIn(self.ev, out)
        self.assertEqual(runner.count("bm_store claim"), 0)
        self.assertEqual(self.asked, [])
        os.chmod(self.ev, 0o700)
        self.assertEqual(C.evidence_write_refusal(self.ev), "")
        self.assertEqual(os.listdir(self.ev), [], "the probe leaves nothing")
        for value in (None, "", 7):
            self.assertTrue(C.evidence_write_refusal(value)
                            .startswith("REFUSED"), value)

    def test_a_stop_record_write_failure_exits_non_zero_naming_stop(self):
        runner = SeamRunner(**{"export_public.py --push":
                               (1, "remote: Blocked by the classifier\n")})
        with mock.patch.object(C, "write_stop_record",
                               side_effect=OSError("disk gone")):
            code, out = self.main([], runner)
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("STOP: export_public.py --push failed (host-refused) "
                      "and the stop record could NOT be written (disk gone)",
                      out)
        self.assertEqual(runner.count("export_public.py --push"), 1)
        self.assertNotIn("reproduce_export.py", runner.calls)
        self.assertNotIn("bm_store complete", runner.calls)

    # --- R-CVE-08: a real cut never reads a redirected evidence folder --------

    def test_a_redirected_evidence_folder_refuses_the_real_cut(self):
        for name in C.CUT_REDIRECT_ENV:
            runner = SeamRunner()
            with mock.patch.dict(os.environ, {name: self.ev}):
                code, out = self.main([], runner)
            self.assertEqual(code, C.EXIT_REFUSED, (name, out))
            self.assertIn(name, out)
            self.assertEqual(runner.count("bm_store claim"), 0)

    # --- control 7: the explicit version binds to derive_next (R-CVE-07) -----

    def test_an_explicit_version_below_the_highest_public_tag_is_refused(self):
        runner = TagRunner(local=[LOWER], remote=[LOWER, HIGHER])
        version, basis = N.derive_next(ROOT, HUB_URL, runner)
        self.assertIsNone(version, basis)
        why = C.version_skip_refusal(ROOT, V, runner=TagRunner(
            local=[LOWER], remote=[LOWER, HIGHER]))
        self.assertTrue(why.startswith("NO-DATA:"), why)
        self.assertIn("older than a released tag", why)
        # the version derive_next answers is accepted
        self.assertEqual(C.version_skip_refusal(ROOT, V, runner=in_flight()),
                         "")

    def test_an_explicit_version_on_a_drifted_carrier_is_refused(self):
        root = os.path.join(self.scratch, "carriers")
        copy_carriers(root)
        self.assertEqual(C.version_skip_refusal(root, V, runner=in_flight()),
                         "")
        codex = os.path.join(root, "bundle", ".codex-plugin", "plugin.json")
        with open(codex, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["version"] = "0.0.0"
        with open(codex, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        why = C.version_skip_refusal(root, V, runner=in_flight())
        self.assertTrue(why.startswith("NO-DATA:"), why)
        self.assertIn("drift", why)
        # and through the real gates, before the fence
        runner = CutRunner(root, local=[LOWER], remote=[LOWER])
        code, out = self.main([], runner, gates=False, root=root)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("drift", out)
        self.assertEqual(runner.claims(), [])


if __name__ == "__main__":
    unittest.main()
