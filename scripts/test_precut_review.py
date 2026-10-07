"""Drives PR1.a and PR1.b of precut_review.py both ways on temporary repositories.

FreezeTest fixtures are real temporary git object stores written directly,
with loose objects, refs under refs/heads and refs/tags, and a HEAD pointer;
the module reads them through git (cat-file and rev-parse), which accepts the
forged base commit the fixtures carry under the recorded provenance sha.
Every failing direction names the defect it exists for: a second freeze, a
moved HEAD, a dirty tree, a stale result, a corrupt record, a moved base tag,
and a candidate on an orphan branch. ReaderTest drives the object reader on
git built repositories: packed objects, a worktree whose git directory holds
no objects of its own, a missing object, and a git that cannot start. SbeTest
drives the pass, which spawns git and the candidate's judges, on the same
fixtures plus git built repositories for the real range scanners.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import precut_review as PR  # noqa: E402

BASE_COMMIT = "97691d2ea7985933179d790d761ba436d94bfac7"


def _write_file(path, text):
    if not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_object(git_dir, sha, kind, content):
    path = os.path.join(git_dir, "objects", sha[:2], sha[2:])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = kind + b" " + str(len(content)).encode("ascii") + b"\x00" + content
    if os.path.exists(path):
        # git writes its loose objects read only; a forged object is the
        # one exception and is always rewritten
        if sha != BASE_COMMIT:
            return
        os.chmod(path, 0o644)
    with open(path, "wb") as handle:
        handle.write(zlib.compress(body))


def _blob(git_dir, data):
    sha = hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()
    _write_object(git_dir, sha, b"blob", data)
    return sha


def _tree(git_dir, entries):
    entries = sorted(entries, key=lambda item: item[0])
    body = b""
    for name, mode, sha in entries:
        body += (mode.encode("ascii") + b" " + name.encode("utf-8") + b"\x00"
                 + bytes.fromhex(sha))
    sha = hashlib.sha1(b"tree %d\x00" % len(body) + body).hexdigest()
    _write_object(git_dir, sha, b"tree", body)
    return sha


def _commit(git_dir, tree, parents, message="fixture"):
    lines = ["tree " + tree]
    for parent in parents:
        lines.append("parent " + parent)
    lines.append("author Fixture <fixture@example.invalid> 1700000000 +0000")
    lines.append("committer Fixture <fixture@example.invalid> 1700000000 +0000")
    lines.append("")
    lines.append(message)
    content = ("\n".join(lines) + "\n").encode("utf-8")
    sha = hashlib.sha1(b"commit %d\x00" % len(content) + content).hexdigest()
    _write_object(git_dir, sha, b"commit", content)
    return sha


def note(cut, name="docs/releases/0.0.1.md"):
    """A release note naming its hub cut commit, in the shape the cut writes;
    cut None gives an uncut note (no cut line), as the candidate writes it."""
    body = "# fixture\n\n" + ("Cut from hub commit `%s` (hub, private).\n" % cut if cut else "")
    return {name: body.encode("utf-8")}


#: The umbrella version a fixture candidate declares (the version being cut).
MARKETPLACE = b'{"name": "fixture", "metadata": {"version": "1.1.0"}, "plugins": []}\n'
#: A candidate that is not the bootstrap version: nothing may judge it
#: while no release base carries the judge.
MARKETPLACE_LATER = b'{"name": "fixture", "metadata": {"version": "1.1.1"}, "plugins": []}\n'


def note_tree(git_dir, notes):
    """A tree holding docs/releases/<name> for every {name: cut} given, the
    shape an anchor commit's tree has; written without a git binary."""
    blobs = [(name, "100644", _blob(git_dir, note(cut, name)[name])) for name, cut in notes.items()]
    releases = _tree(git_dir, blobs)
    return _tree(git_dir, [("docs", "40000", _tree(git_dir, [("releases", "40000", releases)]))])


def forge_tag(root, tree=None):
    """In a git built repository, point the base tag at a forged commit under
    the recorded provenance sha (git reads it through cat-file and refuses it
    elsewhere). The ref is written by hand: update-ref parses the object."""
    git_dir = os.path.join(root, ".git")
    tree = tree or git(root, "rev-parse", "HEAD^{tree}")
    _write_object(git_dir, BASE_COMMIT, b"commit",
                  ("tree %s\nauthor F <f@example.invalid> 1700000000 +0000\n"
                   "committer F <f@example.invalid> 1700000000 +0000\n\nbase\n" % tree).encode("utf-8"))
    os.makedirs(os.path.join(git_dir, "refs", "tags"), exist_ok=True)
    _write_file(os.path.join(git_dir, "refs", "tags", "v1.0.21"), BASE_COMMIT)


def make_repo(root, files=None, head_parents_cut=True):
    """Build a real temporary git repository without a git binary. The base
    tag points at a forged commit under the recorded provenance sha (git reads
    it through cat-file and refuses it elsewhere); the head descends from a
    real root commit that the release note in the tree names as the hub cut,
    so the release side is empty. head_parents_cut=False makes the head an
    orphan that shares no history with that cut."""
    files = dict(files) if files else {"README.txt": b"hello\n"}
    git_dir = os.path.join(root, ".git")
    os.makedirs(os.path.join(git_dir, "objects"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
    os.makedirs(os.path.join(git_dir, "refs", "tags"), exist_ok=True)
    cut = _commit(git_dir, _tree(git_dir, []), [], "cut")
    files.setdefault(".claude-plugin/marketplace.json", MARKETPLACE)
    entries = []
    for name, data in files.items():
        full = os.path.join(root, name)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(data)
        entries.append((name, "100644", _blob(git_dir, data)))
    tree = _tree(git_dir, entries)
    # the forged base commit's tree is the anchor: it carries the note that
    # names the hub cut, which the candidate's own tree never decides
    anchor = note_tree(git_dir, {"0.0.1.md": cut})
    base_body = ("tree %s\n"
                 "author Fixture <fixture@example.invalid> 1700000000 +0000\n"
                 "committer Fixture <fixture@example.invalid> 1700000000 +0000\n\nbase\n"
                 % anchor).encode("utf-8")
    _write_object(git_dir, BASE_COMMIT, b"commit", base_body)
    parents = [cut] if head_parents_cut else []
    head = _commit(git_dir, tree, parents)
    _write_file(os.path.join(git_dir, "refs", "heads", "main"), head)
    _write_file(os.path.join(git_dir, "refs", "tags", "v1.0.21"), BASE_COMMIT)
    _write_file(os.path.join(git_dir, "HEAD"), "ref: refs/heads/main")
    return head, tree


class FreezeTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="precut-review-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(PR._close_readers)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.rec = os.path.join(self.tmp, "rec")
        self.head, self.tree = make_repo(self.repo)

    def freeze_code(self, *extra):
        return PR.main(["--dir", self.rec, "--root", self.repo, "freeze"] + list(extra))

    def test_freeze_writes_once(self):
        self.assertEqual(self.freeze_code(), 0)
        path = os.path.join(self.rec, "freeze.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["sha"], self.head)
        self.assertEqual(record["base_commit"], BASE_COMMIT)
        self.assertEqual(record["base_tag"], "v1.0.21")
        self.assertEqual(record["release_base"], record["shared_base"])
        self.assertEqual(record["release_note"], "docs/releases/0.0.1.md at v1.0.21")
        self.assertEqual(record["judge"], self.head)
        self.assertTrue(record["judge_source"].startswith("bootstrap: no scripts/precut_review.py"))
        self.assertEqual(record["judge_files"], {})
        self.assertEqual(PR.frozen_sha(self.rec), self.head)
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.PASS, detail)

    def tag(self, name, target):
        _write_file(os.path.join(self.repo, ".git", "refs", "tags", name), target)

    def test_bootstrap_is_refused_once_a_cut_tag_exists(self):
        """A v1.1.0 tag exists and the base carries no judge: the candidate
        still declares 1.1.0, but bootstrap is over whatever base it names."""
        self.tag("v1.1.0", BASE_COMMIT)
        with self.assertRaises(PR.NoDataError) as caught:
            PR.freeze(self.repo, self.rec, "v1.0.21")
        self.assertIn("the release tag(s) v1.1.0 exist: bootstrap is over", str(caught.exception))
        self.assertEqual(self.freeze_code("--base-tag", "v1.0.21"), 2)

    def test_retired_line_tags_neither_base_nor_end_bootstrap(self):
        """v3.4.2 is the retired pre-renumber line: never the derived base,
        never a cut that ends bootstrap."""
        self.tag("v3.4.2", self.head)
        self.assertEqual(self.freeze_code(), 0)
        self.assertEqual(PR._load_freeze(self.rec)["base_tag"], "v1.0.21")

    def test_base_tag_is_the_highest_by_version_not_by_text(self):
        """v1.0.9 sorts above v1.0.21 as text; the derived base is v1.0.21."""
        self.tag("v1.0.9", self.head)
        self.tag("v1.0.20-rc.1", self.head)
        self.assertEqual(PR.derived_base_tag(PR._git_dir(self.repo)), "v1.0.21")
        self.assertEqual(self.freeze_code(), 0)
        self.assertEqual(PR._load_freeze(self.rec)["base_tag"], "v1.0.21")

    def test_inherited_git_location_variables_do_not_steer_git(self):
        """GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES and
        GIT_SHALLOW_FILE inherited from the caller point git at an empty
        store; every git call here strips them, so the freeze still reads."""
        empty = os.path.join(self.tmp, "empty-objects")
        os.makedirs(os.path.join(empty, "info"))
        steer = {"GIT_OBJECT_DIRECTORY": empty, "GIT_ALTERNATE_OBJECT_DIRECTORIES": empty,
                 "GIT_SHALLOW_FILE": os.path.join(self.tmp, "no-shallow")}
        PR._close_readers()
        with mock.patch.dict(os.environ, steer):
            self.assertEqual(self.freeze_code(), 0)
            PR._close_readers()
        self.assertEqual(PR.frozen_sha(self.rec), self.head)

    def test_second_freeze_refused(self):
        self.assertEqual(self.freeze_code(), 0)
        self.assertEqual(self.freeze_code(), 1)
        with self.assertRaises(FileExistsError):
            PR.freeze(self.repo, self.rec)

    def test_moved_head_invalidates(self):
        self.assertEqual(self.freeze_code(), 0)
        git_dir = os.path.join(self.repo, ".git")
        moved = _commit(git_dir, self.tree, [self.head])
        _write_file(os.path.join(git_dir, "refs", "heads", "main"), moved)
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.FAIL, detail)

    def test_dirty_tree_invalidates(self):
        self.assertEqual(self.freeze_code(), 0)
        with open(os.path.join(self.repo, "README.txt"), "wb") as handle:
            handle.write(b"changed\n")
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.FAIL, detail)

    def test_stale_result_is_fail(self):
        self.assertEqual(self.freeze_code(), 0)
        results = os.path.join(self.rec, "results")
        os.makedirs(results, exist_ok=True)
        with open(os.path.join(results, "sbe.json"), "w", encoding="utf-8") as handle:
            json.dump({"check": "sbe", "sha": "0" * 40, "verdict": "PASS",
                       "detail": "x"}, handle)
        verdict, detail = PR.read_result(self.rec, "sbe")
        self.assertEqual(verdict, PR.FAIL, detail)

    def test_corrupt_freeze_is_no_data(self):
        os.makedirs(self.rec, exist_ok=True)
        with open(os.path.join(self.rec, "freeze.json"), "wb") as handle:
            handle.write(b"{not json at all")
        self.assertIsNone(PR.frozen_sha(self.rec))
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.NO_DATA, detail)

    def test_replace_clears_results(self):
        self.assertEqual(self.freeze_code(), 0)
        PR.record_result(self.rec, "sbe", PR.PASS, "ok")
        PR.record_result(self.rec, "pristine", PR.PASS, "ok")
        self.assertEqual(PR.read_result(self.rec, "sbe")[0], PR.PASS)
        self.assertTrue(os.path.isdir(os.path.join(self.rec, "results")))
        self.assertEqual(self.freeze_code("--replace"), 0)
        self.assertFalse(os.path.isdir(os.path.join(self.rec, "results")))
        self.assertEqual(PR.read_result(self.rec, "sbe")[0], PR.NO_DATA)
        self.assertEqual(self.freeze_code(), 1)

    def test_moved_base_tag_fails(self):
        self.assertEqual(self.freeze_code(), 0)
        git_dir = os.path.join(self.repo, ".git")
        _write_file(os.path.join(git_dir, "refs", "tags", "v1.0.21"), self.head)
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.FAIL, detail)

    def test_divergent_candidate_refused(self):
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        make_repo(self.repo, head_parents_cut=False)
        self.assertEqual(self.freeze_code(), 1)
        self.assertIsNone(PR.frozen_sha(self.rec))
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("shares no history", str(caught.exception))

    def test_main_refuses_a_non_argv_argument(self):
        for bad in (0, True, -1, float("nan"), b"x", {1, 2}, object()):
            with self.subTest(bad=reprable(bad)):
                self.assertEqual(PR.main(bad), 2)
        self.assertEqual(PR.main(["freeze", 7]), 2)
        self.assertEqual(PR.main([]), 2)


REPO_ROOT = os.path.dirname(HERE)
JUDGES_NAMED_IN_SPEC = (
    "scripts/outgoing_scan.py", "scripts/private_terms_scan.py",
    "products/brothersbe/tools/sbe_gate.py", "products/brothersbe/tools/sbe_score.py",
    "products/brothersbe/tools/sbe_checks.py", "products/brothersbe/tools/sbe_telemetry.py",
    "scripts/check_all.sh", "scripts/required_fast.sh", "scripts/cut_preflight.py",
    "scripts/precut_review.py", "scripts/regen_generated.py", "scripts/bundle_runtime.py",
    "scripts/system_doc.py", "scripts/codex_skills.py", "scripts/test_battery_registration.py",
    "products/brothermode/scripts/checksums.sh", "products/brothersbe/scripts/checksums.sh",
)
GATE = "products/brothersbe/tools/sbe_gate.py"
FAKE_GATE_PASS = b"import sys\nprint('  numbers   PASS     two figures pinned')\nsys.exit(0)\n"
FAKE_GATE_EXIT2 = b"import sys\nprint('  numbers   PASS     two figures pinned')\nsys.exit(2)\n"
FAKE_GATE_NO_DATA = b"import sys\nprint('  numbers   NO-DATA  nothing to read')\nsys.exit(0)\n"
FAKE_OUTGOING_CLEAN = (b"import sys\nprint('outgoing_scan: 0 hit(s) across 1 commit(s)')\n"
                       b"sys.exit(0)\n")
FAKE_OUTGOING_HIT = (b"import sys\nprint('secret HIT-MARKER-7f3 message: sk-abcdefabcdef')\n"
                     b"print('outgoing_scan: 1 hit(s) across 1 commit(s)')\nsys.exit(1)\n")
FAKE_OUTGOING_CHATTY = (b"import sys\nprint('FORWARDED-STDOUT-xyz')\n"
                        b"print('FORWARDED-STDERR-xyz', file=sys.stderr)\n"
                        b"print('outgoing_scan: 0 hit(s) across 1 commit(s)')\nsys.exit(0)\n")
FAKE_PRIVATE_CLEAN = (b"import sys\nprint('PASS: 1 term(s) checked, none present')\n"
                      b"sys.exit(0)\n")
GOOD_WAIVER = b"y = 1  # sbe: allow-silent the fixture names a real reason here\n"
BARE_SWALLOW = b"def f():\n    try:\n        g()\n    except:\n        pass\n"
CLEAN_PY = b"x = 1\n"
FAKE_TERM = "zzfaketermzz"
LINT_TOOLS = ("products/brothersbe/tools/sbe_score.py",
              "products/brothersbe/tools/sbe_checks.py",
              "products/brothersbe/tools/sbe_telemetry.py")


def baseline(count):
    """The reviewed waivers baseline a fixture candidate carries."""
    return json.dumps({"count": count, "sha": "fixture", "taken_at": "2026-10-04"}).encode("utf-8")


def diff_runner(stdout, returncode=0):
    """A runner that answers `git diff --name-only` itself (the forged fixtures
    cannot diff through the tag) and runs everything else for real."""
    def runner(cmd, **kwargs):
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        return subprocess.run(cmd, **kwargs)
    return runner


def real_file(rel):
    with open(os.path.join(REPO_ROOT, rel), "rb") as handle:
        return handle.read()


def base_fixture(**extra):
    files = {
        "README.txt": b"hello\n",
        GATE: FAKE_GATE_PASS,
        "scripts/outgoing_scan.py": FAKE_OUTGOING_CLEAN,
        "scripts/private_terms_scan.py": FAKE_PRIVATE_CLEAN,
        PR.WAIVERS_BASELINE: baseline(0),
        ".claude-plugin/marketplace.json": MARKETPLACE,
    }
    files.update(extra)
    return files


def git(cwd, *args, message=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="f@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="f@example.invalid")
    cmd = ["git"] + list(args)
    if message is not None:
        cmd += ["-m", message]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout.strip()


def make_git_repo(root, first_files, second_files, second_message="second"):
    """A repository built by the git binary, for the real range scanners: two
    commits, the second holding second_files. Returns (first sha, second sha)."""
    git(root, "init", "-q")
    for stage, files, message in ((1, first_files, "first"), (2, second_files, second_message)):
        for name, data in files.items():
            full = os.path.join(root, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(data)
        git(root, "add", "-A")
        git(root, "commit", "-q", "--no-verify", message=message)
    shas = git(root, "log", "--format=%H").splitlines()
    return shas[1], shas[0]


def worktree_head(cwd):
    """The HEAD sha of a worktree directory, read from its own git dir."""
    with open(os.path.join(cwd, ".git"), encoding="utf-8") as handle:
        git_dir = handle.read().split("gitdir:", 1)[1].strip()
    with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as handle:
        return handle.read().strip()


class ReaderTest(unittest.TestCase):
    """The object reader on repositories git built. Every object and ref read
    goes through git, so a packed store and a worktree (whose own git
    directory holds no objects and no refs) read exactly like a loose one, a
    missing object is no object, and a git that cannot start is NO-DATA."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="precut-reader-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(PR._close_readers)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.first, self.second = make_git_repo(
            self.repo, {"README.txt": b"hello\n"}, {"note.txt": b"plain\n"})
        self.branch = git(self.repo, "symbolic-ref", "--short", "HEAD")

    def test_packed_objects_read(self):
        git(self.repo, "repack", "-a", "-d", "-q")
        objects = os.path.join(self.repo, ".git", "objects")
        self.assertFalse(os.path.exists(os.path.join(objects, self.second[:2], self.second[2:])))
        self.assertTrue(os.listdir(os.path.join(objects, "pack")))
        git_dir = PR._git_dir(self.repo)
        kind, content = PR._read_object(git_dir, self.second)
        self.assertEqual(kind, "commit")
        self.assertIn(("parent " + self.first).encode("ascii"), content)
        self.assertEqual(PR._head_sha(self.repo), self.second)
        self.assertEqual(PR._peel_to_commit(git_dir, self.first), self.first)
        tree = PR._committed_tree(git_dir, self.second)
        self.assertEqual(sorted(path for path, _mode, _sha in PR._tree_entries(git_dir, tree)),
                         ["README.txt", "note.txt"])
        self.assertFalse(PR._dirty(self.repo))

    def test_worktree_reads_through_the_common_dir(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.repo, "worktree", "add", "-q", "--detach", wt, self.second)
        git_dir = PR._git_dir(wt)
        self.assertTrue(os.path.realpath(git_dir).startswith(
            os.path.realpath(os.path.join(self.repo, ".git", "worktrees"))), git_dir)
        self.assertFalse(os.path.isdir(os.path.join(git_dir, "objects")))
        self.assertFalse(os.path.isdir(os.path.join(git_dir, "refs", "heads")))
        self.assertEqual(PR._head_sha(wt), self.second)
        self.assertEqual(PR._resolve_ref(git_dir, "refs/heads/" + self.branch), self.second)
        self.assertEqual(PR._read_object(git_dir, self.second)[0], "commit")
        self.assertEqual(PR.release_side(git_dir, self.first, self.second)[0], PR.PASS)
        self.assertFalse(PR._dirty(wt))

    def test_missing_object_is_no_object(self):
        git_dir = PR._git_dir(self.repo)
        self.assertEqual(PR._read_object(git_dir, "0" * 40), (None, None))
        self.assertEqual(PR._read_object(git_dir, "not a sha"), (None, None))
        self.assertIsNone(PR._peel_to_commit(git_dir, "0" * 40))
        self.assertEqual(PR.release_side(git_dir, "0" * 40, self.second)[0], PR.NO_DATA)
        self.assertEqual(PR._read_object(git_dir, self.second)[0], "commit")

    def test_stalled_reader_is_no_object_and_is_killed(self):
        """A git that prints a short answer and then hangs: the bounded read
        turns the stall into no object within the timeout, the reader
        process is killed, and the next read starts a fresh one."""
        fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake)
        script = os.path.join(fake, "git")
        # shell builtins only: PATH holds nothing else. The second read blocks
        # on our open stdin for as long as the reader lives: a true stall.
        _write_file(script, "#!/bin/sh\nread sha\nprintf '%s commit 99\\nabc' \"$sha\"\nread stall\n")
        os.chmod(script, 0o755)
        PR._close_readers()
        git_dir = PR._git_dir(self.repo)

        def unstick():
            # a reader that ignores the timeout would block this test for
            # ever; killing it after 15 s turns that hang into a failure
            proc = PR._READERS.get(git_dir)
            if proc is not None:
                proc.kill()
        watchdog = threading.Timer(15, unstick)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        with mock.patch.dict(os.environ, {"PATH": fake}), \
                mock.patch.object(PR, "_READ_TIMEOUT_S", 0.5):
            started = time.time()
            self.assertEqual(PR._read_object(git_dir, self.second), (None, None))
            self.assertLess(time.time() - started, 10)
            self.assertNotIn(git_dir, PR._READERS)
            self.assertNotIn(git_dir, PR._BUFFERS)
        self.assertEqual(PR._read_object(git_dir, self.second)[0], "commit")

    def test_broken_git_is_no_data(self):
        os.makedirs(os.path.join(self.repo, ".claude-plugin"))
        with open(os.path.join(self.repo, ".claude-plugin", "marketplace.json"), "wb") as handle:
            handle.write(MARKETPLACE)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--no-verify", message="declares a version")
        third = git(self.repo, "rev-parse", "HEAD")
        forge_tag(self.repo, note_tree(os.path.join(self.repo, ".git"), {"0.0.1.md": self.first}))
        rec = os.path.join(self.tmp, "rec")
        self.assertEqual(PR.main(["--dir", rec, "--root", self.repo, "freeze"]), 0)
        self.assertEqual(PR.verify_freeze(self.repo, rec),
                         (PR.PASS, "HEAD is %s and the tree is clean; judged by the candidate's own "
                                   "code: bootstrap" % third))
        PR._close_readers()
        empty = os.path.join(self.tmp, "nobin")
        os.makedirs(empty)
        with mock.patch.dict(os.environ, {"PATH": empty}):
            self.assertEqual(PR._read_object(PR._git_dir(self.repo), self.second), (None, None))
            self.assertIsNone(PR._head_sha(self.repo))
            verdict, detail = PR.verify_freeze(self.repo, rec)
            self.assertEqual(verdict, PR.NO_DATA, detail)
            self.assertEqual(PR.main(["--dir", rec, "--root", self.repo, "verify"]), 2)
            self.assertEqual(PR.main(["--dir", rec, "--root", self.repo, "sbe"]), 2)
        self.assertFalse(os.path.exists(os.path.join(rec, "results")))
        self.assertEqual(PR.verify_freeze(self.repo, rec)[0], PR.PASS)


VERSIONING = "docs/VERSIONING.md"


class ReleaseBaseTest(unittest.TestCase):
    """Owner ruling 2026-10-04 (release base): the freeze's base is the hub
    commit the newest release note names, and every release-side commit since
    the shared base must be on the candidate by patch identity or be the
    release's own version bump. Git built repositories: a root commit, a
    release branch carrying a fix and a version bump, and a candidate branch
    that does or does not carry the fix's patch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="precut-base-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(PR._close_readers)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.rec = os.path.join(self.tmp, "rec")
        git(self.repo, "init", "-q")
        git(self.repo, "checkout", "-q", "-b", "candidate")
        self.root = self.commit({"README.txt": b"hello\n", VERSIONING: b"Current version: 0.0.1\n",
                                 ".claude-plugin/marketplace.json": MARKETPLACE}, "root")
        self.git_dir = os.path.join(self.repo, ".git")
        forge_tag(self.repo)  # the base tag must still peel to the recorded provenance commit
        git(self.repo, "checkout", "-q", "-b", "release")
        self.fix = self.commit({"fix.py": b"x = 1\n"}, "the fix")
        git(self.repo, "checkout", "-q", "candidate")
        # the candidate has work of its own, so a cherry-pick cannot fast forward
        self.commit({"work.py": b"w = 1\n"}, "candidate work")

    def commit(self, files, message):
        for name, data in files.items():
            full = os.path.join(self.repo, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as handle:
                handle.write(data)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--no-verify", message=message)
        return git(self.repo, "rev-parse", "HEAD")

    def bump(self, extra=None, version="0.0.2"):
        git(self.repo, "checkout", "-q", "release")
        files = {VERSIONING: ("Current version: %s\n" % version).encode("ascii")}
        files.update(extra or {})
        cut = self.commit(files, "%s: the version bump and the regenerated manifests" % version)
        git(self.repo, "checkout", "-q", "candidate")
        return cut

    def anchor(self, cut, name="0.0.1.md"):
        """The base tag's tree carries the note naming cut: the only place
        the release base is read from."""
        forge_tag(self.repo, note_tree(self.git_dir, {name: cut}))
        PR._close_readers()

    def freeze_code(self):
        return PR.main(["--dir", self.rec, "--root", self.repo, "freeze"])

    def test_present_by_patch_id_passes(self):
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        self.assertEqual(self.freeze_code(), 0)
        with open(os.path.join(self.rec, "freeze.json"), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["release_base"], cut)
        self.assertEqual(record["shared_base"], self.root)
        self.assertEqual(record["release_note"], "docs/releases/0.0.1.md at v1.0.21")
        self.assertEqual(record["judge"], PR._head_sha(self.repo))
        self.assertIn("bootstrap", record["judge_source"])
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.PASS, detail)
        git_dir = PR._git_dir(self.repo)
        self.assertIn("1 present by patch id, 1 version bump",
                      PR.release_side(git_dir, cut, PR._head_sha(self.repo))[1])

    def test_missing_fix_refuses_naming_it(self):
        cut = self.bump()
        self.anchor(cut)
        self.assertEqual(self.freeze_code(), 1)
        self.assertIsNone(PR.frozen_sha(self.rec))
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertNotIsInstance(caught.exception, PR.NoDataError)
        self.assertIn(self.fix[:12], str(caught.exception))
        self.assertNotIn(cut[:12], str(caught.exception).split("not on the candidate")[1])

    def test_version_bump_exempt_only_when_it_touches_version_files(self):
        cut = self.bump({"scripts/sneaky.py": b"y = 2\n"})
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        self.assertEqual(self.freeze_code(), 1)
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn(cut[:12], str(caught.exception))
        self.assertIn("scripts/sneaky.py, not a version carrier", str(caught.exception))
        git_dir = PR._git_dir(self.repo)
        self.assertEqual(PR._bump_exempt(git_dir, cut)[0], False)
        self.assertEqual(PR._bump_exempt(git_dir, self.fix), (False, "subject is not a version bump"))
        self.assertIsNone(PR._bump_exempt(git_dir, "0" * 40)[0])
        pure = self.bump(version="0.0.3")
        self.assertEqual(PR._bump_exempt(git_dir, pure), (True, ""))

    def test_bump_that_retargets_a_marketplace_source_refuses(self):
        git(self.repo, "checkout", "-q", "release")
        self.commit({".claude-plugin/marketplace.json":
                     b'{"version": "0.0.1", "source": "github:khalilmaaouni/Brother"}\n'}, "manifest")
        git(self.repo, "checkout", "-q", "candidate")
        cut = self.bump({".claude-plugin/marketplace.json":
                         b'{"version": "0.0.2", "source": "github:someone-else/Brother"}\n'})
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        self.assertEqual(self.freeze_code(), 1)
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        message = str(caught.exception)
        self.assertIn(cut[:12], message)
        self.assertIn(".claude-plugin/marketplace.json changes a field that is not version or ref at", message)
        self.assertIn("someone-else", message)

    def test_bump_that_moves_a_ref_down_or_off_subject_refuses(self):
        manifest = ".claude-plugin/marketplace.json"
        git(self.repo, "checkout", "-q", "release")
        self.commit({manifest: b'{\n  "version": "0.0.1",\n  "ref": "v0.0.1",\n  "url": "https://x.invalid/r"\n}\n'},
                    "manifest")
        git(self.repo, "checkout", "-q", "candidate")
        git_dir = PR._git_dir(self.repo)
        down = self.bump({manifest: b'{\n  "version": "0.0.2",\n  "ref": "v0.0.0",\n  "url": "https://x.invalid/r"\n}\n'})
        verdict, reason = PR._bump_exempt(git_dir, down)
        self.assertEqual(verdict, False)
        self.assertIn("moves a version to 0.0.0, not the 0.0.2 the subject names", reason)
        off = self.bump({manifest: b'{\n  "version": "0.0.3",\n  "ref": "v0.0.3",\n  "url": "https://x.invalid/r"\n}\n'},
                        version="0.0.3")
        self.assertEqual(PR._bump_exempt(git_dir, off), (True, ""))
        sideways = self.bump({manifest: b'{\n  "version": "0.0.3",\n  "ref": "v0.0.3",\n  "url": "https://x.invalid/s"\n}\n'},
                             version="0.0.4")
        verdict, reason = PR._bump_exempt(git_dir, sideways)
        self.assertEqual(verdict, False)
        self.assertIn("changes a field that is not version or ref", reason)
        self.assertIn("x.invalid/s", reason)
        self.assertEqual(PR._version_step("v 1.2.3", "v 1.2.2", "1.2.2"),
                         "moves a version down or sideways from 1.2.3 to 1.2.2")

    def test_bump_that_edits_logic_in_the_facts_module_refuses(self):
        facts = "products/brothermode/tools/bm_project_facts.py"
        git(self.repo, "checkout", "-q", "release")
        self.commit({facts: b'PUBLIC_INSTALL_TAG = "v0.0.1"\nLIMIT = 1\n'}, "facts")
        git(self.repo, "checkout", "-q", "candidate")
        cut = self.bump({facts: b'PUBLIC_INSTALL_TAG = "v0.0.2"\nLIMIT = 2\n'})
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("%s changes more than a version string at 'LIMIT = 2'" % facts,
                      str(caught.exception))
        # the same file bumped in its version string alone is a pure bump
        pure = self.bump({facts: b'PUBLIC_INSTALL_TAG = "v0.0.3"\nLIMIT = 2\n'}, version="0.0.3")
        self.assertEqual(PR._bump_exempt(PR._git_dir(self.repo), pure), (True, ""))

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def test_pure_bump_with_manifest_digests_passes(self):
        """A regenerated CHECKSUMS.sha256 is judged against the tree: every
        added line must carry the sha256 of the file at the bump commit (the
        real 1.0.21 bump gained an entry for a file it never touched), a
        line dropped outright must name a file absent there; the json
        manifests are judged by normalised equality."""
        manifest = "products/brothermode/CHECKSUMS.sha256"
        readme = "products/brothermode/README.md"
        runtime = "bundle/runtime/RUNTIME-MANIFEST.json"
        old_readme, new_readme = b"Install v0.0.1\n", b"Install v0.0.2\n"
        stray = b"x = 1\n"
        runtime_file = b"r = 1\n"

        def runtime_json(version, describe, revision, digest):
            return ('{"version": "%s", "source_describe": "v%s-3-g%s-dirty", "source_revision": "%s",\n'
                    ' "files": [{"path": "r.py", "sha256": "%s"}]}\n'
                    % (version, version, describe, revision, digest)).encode("ascii")
        git(self.repo, "checkout", "-q", "release")
        self.commit({readme: old_readme, "products/brothermode/tools/stray.py": stray,
                     "bundle/runtime/r.py": runtime_file,
                     manifest: ("%s  README.md\n" % self.digest(old_readme)).encode("ascii"),
                     runtime: runtime_json("0.0.1", "1" * 9, "1" * 40, self.digest(runtime_file))},
                    "manifests")
        git(self.repo, "checkout", "-q", "candidate")
        git_dir = PR._git_dir(self.repo)
        # the regeneration re-digests the bumped README and picks up the stray
        cut = self.bump({readme: new_readme,
                         manifest: ("%s  README.md\n%s  tools/stray.py\n"
                                    % (self.digest(new_readme), self.digest(stray))).encode("ascii"),
                         runtime: runtime_json("0.0.2", "2" * 9, "2" * 40, self.digest(runtime_file))})
        self.assertEqual(PR._bump_exempt(git_dir, cut), (True, ""))
        # a runtime manifest digest that is not the file's is a hand edit,
        # whatever the paired-line normalisation would say
        forged = self.bump({runtime: runtime_json("0.0.2", "3" * 9, "3" * 40, "e" * 64)})
        verdict, reason = PR._bump_exempt(git_dir, forged)
        self.assertEqual(verdict, False)
        self.assertIn("%s is not a regeneration" % runtime, reason)
        self.assertIn("is not the sha256 of r.py", reason)
        # a wrong digest is a hand edit
        wrong = self.bump({manifest: ("%s  README.md\n%s  tools/stray.py\n"
                                      % ("b" * 64, self.digest(stray))).encode("ascii")})
        verdict, reason = PR._bump_exempt(git_dir, wrong)
        self.assertEqual(verdict, False)
        self.assertIn("%s is not a regeneration" % manifest, reason)
        self.assertIn("is not the sha256 of README.md", reason)
        # an entry for a file that does not exist is a hand edit
        ghost = self.bump({manifest: ("%s  README.md\n%s  tools/stray.py\n%s  docs/GHOST.md\n"
                                      % (self.digest(new_readme), self.digest(stray), "c" * 64)).encode("ascii")})
        verdict, reason = PR._bump_exempt(git_dir, ghost)
        self.assertEqual(verdict, False)
        self.assertIn("names docs/GHOST.md, absent at", reason)
        # dropping the entry of a file that still exists is a hand edit
        dropped = self.bump({manifest: ("%s  README.md\n" % self.digest(new_readme)).encode("ascii")})
        verdict, reason = PR._bump_exempt(git_dir, dropped)
        self.assertEqual(verdict, False)
        self.assertIn("drops tools/stray.py, which still exists at", reason)
        # a digest is noise only inside a generated manifest: the same change
        # in docs/VERSIONING.md is a content change
        self.bump({VERSIONING: ("Current version: 0.0.5 at %s\n" % ("1" * 40)).encode("ascii")},
                  version="0.0.5")
        hexed = self.bump({VERSIONING: ("Current version: 0.0.5 at %s\n" % ("2" * 40)).encode("ascii")},
                          version="0.0.5")
        verdict, reason = PR._bump_exempt(PR._git_dir(self.repo), hexed)
        self.assertEqual(verdict, False)
        self.assertIn("docs/VERSIONING.md changes more than a version string", reason)
        for path in ("docs/VERSIONING.md", ".claude-plugin/marketplace.json",
                     "bundle/.codex-plugin/plugin.json", "products/brothermode/CHECKSUMS.sha256",
                     "products/brothersbe/docs/RELEASE.md", "bundle/runtime/RUNTIME-MANIFEST.json"):
            self.assertTrue(PR._VERSION_CARRIER_RE.match(path), path)
        for path in ("scripts/sneaky.py", "README.md", "docs/plan/x.md", "products/x/docs/OTHER.md"):
            self.assertIsNone(PR._VERSION_CARRIER_RE.match(path), path)

    def test_bootstrap_is_bound_to_its_version(self):
        """No judge at the release base and a candidate that is not 1.1.0:
        nothing may judge it, so the freeze is NO-DATA and names why."""
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        self.assertEqual(self.freeze_code(), 0)
        shutil.rmtree(self.rec)
        self.commit({".claude-plugin/marketplace.json": MARKETPLACE_LATER}, "declares 1.1.1")
        self.assertEqual(self.freeze_code(), 2)
        with self.assertRaises(PR.NoDataError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("the candidate declares 1.1.1, not the bootstrap version 1.1.0",
                      str(caught.exception))
        self.assertEqual(PR.judge_anchor(self.git_dir, cut, PR._head_sha(self.repo))[0], None)

    def test_a_replace_ref_does_not_fool_the_reader(self):
        """`git replace <hub cut> <fake>` where the fake's tree carries a
        later note naming the root: every git call here ignores replace
        refs, so the base stays the real cut and the fake is never read."""
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        git(self.repo, "checkout", "-q", "-b", "fake", cut)
        fake = self.commit(note(self.root, "docs/releases/0.0.9.md"), "fake cut with a later note")
        git(self.repo, "checkout", "-q", "candidate")
        git(self.repo, "replace", cut, fake)
        PR._close_readers()
        git_dir = PR._git_dir(self.repo)
        self.assertEqual(PR.release_base(git_dir, "v1.0.21"),
                         (cut, "docs/releases/0.0.1.md at v1.0.21", (0, 0, 1)))
        self.assertNotIn(b"fake cut", PR._read_object(git_dir, cut)[1])
        self.assertEqual(PR.release_side(git_dir, cut, PR._head_sha(self.repo))[0], PR.PASS)
        self.assertEqual(self.freeze_code(), 0)
        # git itself, without the guard, does read the fake: the guard is live
        plain_env = dict((k, v) for k, v in os.environ.items() if k != "GIT_NO_REPLACE_OBJECTS")
        plain = subprocess.run(["git", "cat-file", "-p", cut], cwd=self.repo, capture_output=True,
                               text=True, env=plain_env)
        self.assertIn("fake cut", plain.stdout)
        self.assertEqual(PR._git_env()["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(PR._git_env({"X": "1"}), {"X": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                                                   "GIT_GRAFT_FILE": os.devnull})

    def test_an_unreadable_notes_subtree_is_no_data_never_pass(self):
        """The candidate commits an uncut note, then the loose tree object of
        its docs/releases subtree is removed: the notes cannot be read, and
        that is NO-DATA (blocking), never an empty set that passes."""
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        head = self.commit(note(None, "docs/releases/1.1.0.md"), "uncut note")
        git_dir = PR._git_dir(self.repo)
        self.assertEqual(PR.note_drift(git_dir, head, "v1.0.21", cut, (0, 0, 1))[0], PR.PASS)
        subtree = git(self.repo, "rev-parse", "HEAD:docs/releases")
        os.chmod(os.path.join(self.git_dir, "objects", subtree[:2]), 0o755)
        os.remove(os.path.join(self.git_dir, "objects", subtree[:2], subtree[2:]))
        PR._close_readers()
        self.assertIsNone(PR._notes_at(git_dir, head))
        with self.assertRaises(PR.NoDataError):
            list(PR._tree_entries(git_dir, subtree))
        verdict, detail = PR.note_drift(git_dir, head, "v1.0.21", cut, (0, 0, 1))
        self.assertEqual(verdict, PR.NO_DATA, detail)
        self.assertIn("could not be read", detail)
        self.assertTrue(PR._dirty(self.repo))

    def test_no_note_is_no_data(self):
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        # the tag's tree carries no note: a note the candidate commits does
        # not count, whatever it names
        self.commit(note(cut, "docs/releases/0.0.1.md"), "candidate note")
        self.assertEqual(self.freeze_code(), 2)
        self.assertIsNone(PR.frozen_sha(self.rec))
        with self.assertRaises(PR.NoDataError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("no release note at v1.0.21 names a hub cut commit", str(caught.exception))
        self.anchor("f" * 40)
        self.assertEqual(self.freeze_code(), 2)
        with self.assertRaises(PR.NoDataError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("unknown to git", str(caught.exception))

    def test_a_candidate_note_never_decides_the_base(self):
        """Notes are read only at the base tag's tree and at the hub cut it
        names. A note the candidate adds, whatever it names, does not move
        the base and FAILs the freeze naming it; the note for the version
        being cut may exist uncut; the hub cut's own later note wins."""
        cut = self.bump()
        git(self.repo, "cherry-pick", self.fix)
        self.anchor(cut)
        git_dir = PR._git_dir(self.repo)
        self.assertEqual(PR.release_base(git_dir, "v1.0.21"),
                         (cut, "docs/releases/0.0.1.md at v1.0.21", (0, 0, 1)))
        # a note naming the root, higher than any anchor note
        shadow = self.commit(note(self.root, "docs/releases/9.9.9.md"), "shadow note")
        self.assertEqual(PR.release_base(git_dir, "v1.0.21"),
                         (cut, "docs/releases/0.0.1.md at v1.0.21", (0, 0, 1)))
        self.assertEqual(self.freeze_code(), 1)
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("docs/releases/9.9.9.md is not at v1.0.21 or the hub cut", str(caught.exception))
        git(self.repo, "reset", "-q", "--hard", shadow + "^")
        # the note for the version being cut (0.0.2) may exist without a cut line
        self.commit(note(None, "docs/releases/1.1.0.md"), "uncut note for the cut")
        self.assertEqual(self.freeze_code(), 0)
        with open(os.path.join(self.rec, "freeze.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["release_base"], cut)
        # but not with a cut line
        self.commit(note(self.root, "docs/releases/1.1.0.md"), "pre-cut line")
        verdict, detail = PR.verify_freeze(self.repo, self.rec)
        self.assertEqual(verdict, PR.FAIL, detail)
        shutil.rmtree(self.rec)
        with self.assertRaises(ValueError) as caught:
            PR.freeze(self.repo, self.rec)
        self.assertIn("docs/releases/1.1.0.md names a cut commit before the cut", str(caught.exception))
        # an anchor note edited in prose only is listed, an edited cut line fails
        git(self.repo, "reset", "-q", "--hard", shadow + "^")
        self.commit({"docs/releases/0.0.1.md": note(cut)["docs/releases/0.0.1.md"] + b"\nmore prose\n"},
                    "prose edit")
        verdict, detail = PR.note_drift(git_dir, PR._head_sha(self.repo), "v1.0.21", cut, (0, 0, 1))
        self.assertEqual(verdict, PR.PASS, detail)
        self.assertIn("same cut line: docs/releases/0.0.1.md", detail)
        self.commit(note(self.root, "docs/releases/0.0.1.md"), "cut line edit")
        verdict, detail = PR.note_drift(git_dir, PR._head_sha(self.repo), "v1.0.21", cut, (0, 0, 1))
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("docs/releases/0.0.1.md names %s, the anchor copy does not" % self.root, detail)
        # a later note in the hub cut's own tree wins over the tag's
        git(self.repo, "reset", "-q", "--hard", shadow + "^")
        git(self.repo, "checkout", "-q", "release")
        later = self.commit(note(cut, "docs/releases/0.0.5.md"), "the hub cut carries a later note")
        git(self.repo, "checkout", "-q", "candidate")
        self.anchor(later)
        self.assertEqual(PR.release_base(git_dir, "v1.0.21"),
                         (cut, "docs/releases/0.0.5.md at the hub cut %s" % later[:12], (0, 0, 5)))
        # a candidate that declares no version is NO-DATA
        self.commit({".claude-plugin/marketplace.json": b"{}\n"}, "no version")
        self.assertEqual(self.freeze_code(), 2)

class SbeTest(unittest.TestCase):
    """PR1.b: the BrotherSBE pass, each row keyed to the frozen sha and run
    inside a detached worktree. The hand built fixtures forge the base commit,
    so git refuses any read through the tag on them; the rows that need a real
    range (the two scanners) are driven on a git built repository through
    range_scans directly, and the pass fixtures carry stand-in scanners."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="precut-sbe-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(PR._close_readers)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.rec = os.path.join(self.tmp, "rec")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.scratch = os.path.join(self.tmp, "scratch")
        saved = {k: os.environ.get(k) for k in ("HOME", "BROTHER_SCRATCH")}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        os.environ["HOME"] = self.home
        os.environ["BROTHER_SCRATCH"] = self.scratch
        self.terms = os.path.join(self.home, ".brothersbe-private-names")
        _write_file(self.terms, FAKE_TERM)

    def run_pass(self, files, runner=None, landed=None, reviewed=True):
        """Freeze the fixture and run the pass. The fixture's release base is
        a root with no baseline file, so the baseline anchor is the pinned
        landing commit, patched to the fixture head unless landed says
        otherwise."""
        self.head, self.tree = make_repo(self.repo, files)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze"]), 0)
        if reviewed:
            # the fixture's release base is an empty root, so every judge
            # file counts as changed: the owner's hand records the review
            self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        patch = mock.patch.object(PR, "WAIVERS_BASELINE_LANDED", landed or self.head)
        patch.start()
        self.addCleanup(patch.stop)
        rows = PR.sbe_pass(self.repo, self.rec, runner)
        return dict((name, (verdict, detail)) for verdict, name, detail in rows)

    def test_baseline_pin_outside_the_candidate_is_no_data(self):
        rows = self.run_pass(base_fixture(**{"g.py": GOOD_WAIVER, PR.WAIVERS_BASELINE: baseline(1)}),
                             self.tag_grep_runner, landed="f" * 40)
        self.assertEqual(rows["waivers"][0], PR.NO_DATA, rows["waivers"])
        self.assertIn("not an ancestor of the candidate", rows["waivers"][1])

    def test_baseline_raised_after_its_anchor_fails_naming_the_commit(self):
        """A git built candidate: the release base carries baseline 0, a
        later commit adds a marker and raises the file to 5. The row must
        read the baseline at the release base and FAIL on the edit, never
        read the raised copy from the worktree."""
        first, second = make_git_repo(
            self.repo,
            dict(base_fixture(), **{PR.WAIVERS_BASELINE: baseline(0)}),
            {"g.py": GOOD_WAIVER, PR.WAIVERS_BASELINE: baseline(5)})
        # the anchor note names the first commit as the hub cut
        forge_tag(self.repo, note_tree(os.path.join(self.repo, ".git"), {"0.0.1.md": first}))
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze"]), 0)
        rows = dict((name, (v, d)) for v, name, d in
                    PR.sbe_pass(self.repo, self.rec, self.tag_grep_runner))
        verdict, detail = rows["waivers"]
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("edited after the release base %s" % first[:12], detail)
        self.assertIn(second[:7], detail)
        self.assertNotIn("reviewed baseline 5", detail)

    def test_gate_pass_is_recorded_under_the_sha(self):
        rows = self.run_pass(base_fixture())
        self.assertEqual(rows["sbe-gate"][0], PR.PASS, rows["sbe-gate"])
        self.assertEqual(PR.read_result(self.rec, "sbe-gate")[0], PR.PASS)
        with open(os.path.join(self.rec, "results", "sbe-gate.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["sha"], self.head)
        self.assertEqual(sorted(rows), sorted(PR.SBE_ROWS))

    def test_gate_no_data_is_not_pass(self):
        rows = self.run_pass(base_fixture(**{GATE: FAKE_GATE_EXIT2}))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])

    def test_gate_exit0_printing_no_data_is_no_data(self):
        rows = self.run_pass(base_fixture(**{GATE: FAKE_GATE_NO_DATA}))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])

    def test_gate_silent_exit0_is_no_data(self):
        rows = self.run_pass(base_fixture(**{GATE: b"import sys\nsys.exit(0)\n"}))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])

    def test_unreadable_checkout_is_a_disturbance(self):
        self.run_pass(base_fixture())
        frozen = PR._fingerprint(self.repo)
        self.assertIsNone(PR._disturbed(self.repo, self.head, frozen))
        gone = os.path.join(self.tmp, "gone")
        self.assertEqual(PR._disturbed(gone, self.head, frozen),
                         "the main checkout could not be read during the pass")

    def test_empty_waiver_reason_fails(self):
        rows = self.run_pass(base_fixture(**{"w.py": b"y = 1  # sbe: allow-silent\n",
                                             "s.py": b"z = 1  # sbe: allow-silent too short\n"}))
        self.assertEqual(rows["waivers"][0], PR.FAIL, rows["waivers"])
        self.assertIn("w.py:1", rows["waivers"][1])
        self.assertIn("s.py:1", rows["waivers"][1])

    def test_waiver_rows_shape(self):
        make_git_repo(self.repo, {"w.py": b"y = 1  # sbe: allow-silent\n"},
                      {"s.py": b"z = 1  # sbe: allow-silent too short\n", "g.py": GOOD_WAIVER})
        self.assertEqual(sorted(PR.waiver_rows(self.repo)), [
            ("g.py", 1, "the fixture names a real reason here"),
            ("s.py", 1, "too short"), ("w.py", 1, "")])
        with self.assertRaises(ValueError):
            PR.waiver_rows(self.tmp)

    @staticmethod
    def tag_grep_runner(cmd, **kwargs):
        """The forged fixtures cannot grep through the tag: answer no marker at it."""
        if cmd[:2] == ["git", "grep"] and cmd[-1] == "v1.0.21":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.run(cmd, **kwargs)

    def test_waiver_rise_lists_new_reasons(self):
        rows = self.run_pass(base_fixture(**{"g.py": GOOD_WAIVER}), self.tag_grep_runner)
        self.assertEqual(rows["waivers"][0], PR.FAIL, rows["waivers"])
        self.assertIn("above the reviewed baseline 0", rows["waivers"][1])
        self.assertIn("g.py:1 the fixture names a real reason here", rows["waivers"][1])

    def test_waivers_at_baseline_pass_and_list_new(self):
        rows = self.run_pass(base_fixture(**{"g.py": GOOD_WAIVER, PR.WAIVERS_BASELINE: baseline(1)}),
                             self.tag_grep_runner)
        self.assertEqual(rows["waivers"][0], PR.PASS, rows["waivers"])
        self.assertIn("reviewed baseline 1", rows["waivers"][1])
        self.assertIn("new since v1.0.21: g.py:1 the fixture names a real reason here",
                      rows["waivers"][1])

    def test_missing_waivers_baseline_is_no_data(self):
        files = base_fixture(**{"g.py": GOOD_WAIVER})
        del files[PR.WAIVERS_BASELINE]
        rows = self.run_pass(files, self.tag_grep_runner)
        self.assertEqual(rows["waivers"][0], PR.NO_DATA, rows["waivers"])
        self.assertIn("no readable reviewed baseline", rows["waivers"][1])
        corrupt = base_fixture(**{"g.py": GOOD_WAIVER, PR.WAIVERS_BASELINE: b'{"count": "1"}'})
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        shutil.rmtree(self.rec)
        rows = self.run_pass(corrupt, self.tag_grep_runner)
        self.assertEqual(rows["waivers"][0], PR.NO_DATA, rows["waivers"])

    def test_edited_scanner_is_listed(self):
        rows = self.run_pass(base_fixture())
        self.assertEqual(rows["scanner-drift"][0], PR.PASS, rows["scanner-drift"])
        self.assertIn("scripts/outgoing_scan.py", json.loads(rows["scanner-drift"][1]))

    def test_every_named_judge_is_in_judge_files(self):
        planted = dict((path, b"x\n") for path in JUDGES_NAMED_IN_SPEC)
        rows = self.run_pass(base_fixture(**planted))
        self.assertEqual(rows["scanner-drift"][0], PR.PASS, rows["scanner-drift"])
        listed = json.loads(rows["scanner-drift"][1])
        for path in JUDGES_NAMED_IN_SPEC:
            self.assertIn(path, listed)

    def test_edited_check_script_is_listed(self):
        rows = self.run_pass(base_fixture(**{"scripts/system_doc.py": b"print(1)\n"}))
        self.assertIn("scripts/system_doc.py", json.loads(rows["scanner-drift"][1]))

    def test_planted_silent_failure_fails_silent_lint(self):
        tools = dict((rel, real_file(rel)) for rel in LINT_TOOLS)
        rows = self.run_pass(base_fixture(**dict(tools, **{"bad.py": BARE_SWALLOW})),
                             diff_runner("bad.py\0"))
        self.assertEqual(rows["silent-lint"][0], PR.FAIL, rows["silent-lint"])
        self.assertIn("bad.py", rows["silent-lint"][1])
        self.assertIn("range v1.0.21..", rows["silent-lint"][1])

    def test_lint_hit_outside_the_range_is_reported_not_failed(self):
        tools = dict((rel, real_file(rel)) for rel in LINT_TOOLS)
        rows = self.run_pass(base_fixture(**dict(tools, **{"bad.py": BARE_SWALLOW, "ok.py": CLEAN_PY})),
                             diff_runner("ok.py\0"))
        verdict, detail = rows["silent-lint"]
        self.assertEqual(verdict, PR.PASS, detail)
        self.assertIn("whole tree FAIL (reported, not gated): 1 hit(s)", detail)
        self.assertIn("bad.py", detail)

    def test_unreadable_range_is_no_data_for_lint(self):
        tools = dict((rel, real_file(rel)) for rel in LINT_TOOLS)
        rows = self.run_pass(base_fixture(**dict(tools, **{"bad.py": BARE_SWALLOW})),
                             diff_runner("", 128))
        self.assertEqual(rows["silent-lint"][0], PR.NO_DATA, rows["silent-lint"])
        self.assertIn("could not be read", rows["silent-lint"][1])

    def test_lint_range_through_real_git_diff(self):
        tools = dict((rel, real_file(rel)) for rel in LINT_TOOLS)
        first, second = make_git_repo(self.repo, dict(tools, **{"bad.py": BARE_SWALLOW}),
                                      {"ok.py": CLEAN_PY})
        env = PR._candidate_env(self.home, self.rec)
        verdict, detail = PR._lint_row(self.repo, self.repo, env, first, second, None)
        self.assertEqual(verdict, PR.PASS, detail)
        self.assertIn("(1 changed file(s))", detail)
        self.assertIn("whole tree FAIL", detail)
        third = self.repo
        with open(os.path.join(third, "worse.py"), "wb") as handle:
            handle.write(BARE_SWALLOW)
        git(third, "add", "-A")
        git(third, "commit", "-q", "--no-verify", message="third")
        head = git(third, "rev-parse", "HEAD")
        verdict, detail = PR._lint_row(self.repo, self.repo, env, first, head, None)
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("worse.py", detail)
        self.assertEqual(os.listdir(self.scratch) if os.path.isdir(self.scratch) else [], [])

    def test_missing_terms_file_is_no_data(self):
        os.remove(self.terms)
        rows = self.run_pass(base_fixture())
        self.assertEqual(rows["scan-range"][0], PR.NO_DATA, rows["scan-range"])
        for verdict, _name, _detail in PR.range_scans(self.repo, "v1.0.21", self.head):
            self.assertEqual(verdict, PR.NO_DATA)

    def test_empty_terms_file_is_no_data(self):
        _write_file(self.terms, "# only a comment line")
        rows = self.run_pass(base_fixture())
        self.assertEqual(rows["scan-range"][0], PR.NO_DATA, rows["scan-range"])

    def test_scan_hit_is_counted_not_printed(self):
        rows = self.run_pass(base_fixture(**{"scripts/outgoing_scan.py": FAKE_OUTGOING_HIT}))
        verdict, detail = rows["scan-range"]
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("1 hit(s)", detail)
        self.assertNotIn("HIT-MARKER-7f3", detail)
        self.assertNotIn("sk-abcdef", detail)

    def test_scan_output_not_forwarded(self):
        rows = self.run_pass(base_fixture(**{"scripts/outgoing_scan.py": FAKE_OUTGOING_CHATTY}))
        verdict, detail = rows["scan-range"]
        self.assertEqual(verdict, PR.PASS, detail)
        with open(os.path.join(self.rec, "results", "scan-range.json"), encoding="utf-8") as handle:
            stored = handle.read()
        for marker in ("FORWARDED-STDOUT-xyz", "FORWARDED-STDERR-xyz"):
            self.assertNotIn(marker, detail)
            self.assertNotIn(marker, stored)

    def test_planted_private_term_is_counted(self):
        first, second = make_git_repo(
            self.repo,
            {"README.txt": b"hello\n", "scripts/outgoing_scan.py": FAKE_OUTGOING_CLEAN,
             "scripts/private_terms_scan.py": real_file("scripts/private_terms_scan.py")},
            {"note.txt": ("the %s appears here\n" % FAKE_TERM).encode("utf-8")})
        rows = dict((name, (v, d)) for v, name, d in PR.range_scans(self.repo, first, second))
        self.assertEqual(rows["private-terms"][0], PR.FAIL, rows["private-terms"])
        self.assertIn("1 hit(s)", rows["private-terms"][1])
        self.assertNotIn(FAKE_TERM, rows["private-terms"][1])

    def test_planted_dash_and_trailer_are_counted(self):
        trailer = "Co-" + "Authored" + "-By: Someone " + "Fa" + "ble <s@example.invalid>"
        first, second = make_git_repo(
            self.repo,
            {"README.txt": b"hello\n", "scripts/outgoing_scan.py": real_file("scripts/outgoing_scan.py"),
             "scripts/private_terms_scan.py": FAKE_PRIVATE_CLEAN},
            {"note.txt": b"plain\n"},
            second_message="fix " + chr(0x2014) + " thing\n\n" + trailer)
        rows = dict((name, (v, d)) for v, name, d in PR.range_scans(self.repo, first, second))
        self.assertEqual(rows["outgoing-scan"][0], PR.FAIL, rows["outgoing-scan"])
        self.assertIn("2 hit(s)", rows["outgoing-scan"][1])
        self.assertNotIn("Someone", rows["outgoing-scan"][1])

    def _move_head(self, back=False):
        git_dir = os.path.join(self.repo, ".git")
        target = self.head if back else _commit(git_dir, self.tree, [self.head], "moved")
        _write_file(os.path.join(git_dir, "refs", "heads", "main"), target)

    def test_head_moves_during_pass(self):
        def runner(cmd, **kwargs):
            if cmd[:2] == [sys.executable, "-I"] and cmd[2].endswith("outgoing_scan.py"):
                self._move_head()
            return subprocess.run(cmd, **kwargs)
        rows = self.run_pass(base_fixture(), runner)
        self.assertEqual(rows["sbe-gate"][0], PR.PASS, rows["sbe-gate"])
        self.assertEqual(rows["scanner-drift"][0], PR.PASS, rows["scanner-drift"])
        self.assertEqual(rows["scan-range"], (PR.FAIL, "tree changed during the pass"))
        self.assertEqual(PR.read_result(self.rec, "scan-range")[0], PR.FAIL)

    def test_move_away_and_back_is_seen(self):
        def runner(cmd, **kwargs):
            if cmd[:2] == [sys.executable, "-I"] and cmd[2].endswith("sbe_gate.py"):
                self._move_head()
            if cmd[:3] == ["git", "worktree", "remove"]:
                self._move_head(back=True)
            return subprocess.run(cmd, **kwargs)
        rows = self.run_pass(base_fixture(), runner)
        self.assertEqual(PR._head_sha(self.repo), self.head)
        for name in PR.SBE_ROWS:
            self.assertEqual(rows[name], (PR.FAIL, "tree changed during the pass"), name)

    def test_rows_run_in_detached_worktree_at_frozen_sha(self):
        seen = []

        def runner(cmd, **kwargs):
            if cmd[0] == sys.executable:
                seen.append((cmd, kwargs.get("cwd"), worktree_head(kwargs.get("cwd"))))
            return subprocess.run(cmd, **kwargs)
        rows = self.run_pass(base_fixture(), runner)
        self.assertEqual(rows["sbe-gate"][0], PR.PASS, rows["sbe-gate"])
        self.assertEqual(len(seen), 3, seen)
        for cmd, cwd, head_at_call in seen:
            self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.repo), cmd)
            self.assertTrue(cwd.startswith(self.scratch), cwd)
            self.assertEqual(head_at_call, self.head, cmd)
        self.assertFalse(os.path.exists(seen[0][1]))
        self.assertEqual(os.listdir(self.scratch), [])

    def test_sbe_without_freeze_writes_nothing(self):
        make_repo(self.repo, base_fixture())
        rows = PR.sbe_pass(self.repo, self.rec)
        self.assertEqual([row[0] for row in rows], [PR.NO_DATA])
        self.assertFalse(os.path.exists(os.path.join(self.rec, "results")))
        # the owner's hand needs a PASS freeze too
        os.makedirs(self.rec, exist_ok=True)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.rec, PR.JUDGE_REVIEW)))

    def test_sha_current_sees_an_edited_judge(self):
        self.run_pass(base_fixture())
        self.assertEqual(PR.sha_current(self.repo, self.rec)[0], PR.PASS)
        with open(os.path.join(self.repo, "scripts", "outgoing_scan.py"), "ab") as handle:
            handle.write(b"# edited after the freeze\n")
        verdict, detail = PR.sha_current(self.repo, self.rec)
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("scripts/outgoing_scan.py", detail)

    def judge_repo(self, module=None, first_extra=None, second_extra=None):
        """A git built candidate whose release base carries the judge: this
        module with a marker in its gate summary (or the module given) and
        the git location list it imports, plus the stand-in judges; the
        candidate then replaces its own outgoing scanner with one that
        reports a hit. Returns (release base, candidate)."""
        if module is None:
            module = real_file("scripts/precut_review.py").replace(
                b'summary = "exit %d; gates: %s" % (', b'summary = "judge-copy exit %d; gates: %s" % (')
            self.assertNotEqual(module, real_file("scripts/precut_review.py"))
        first_files = dict(base_fixture(), **{"scripts/precut_review.py": module,
                                              "scripts/tmp_sandbox.py": real_file("scripts/tmp_sandbox.py")})
        first_files.update(first_extra or {})
        second_files = {"scripts/outgoing_scan.py": FAKE_OUTGOING_HIT}
        second_files.update(second_extra or {})
        first, second = make_git_repo(self.repo, first_files, second_files)
        forge_tag(self.repo, note_tree(os.path.join(self.repo, ".git"), {"0.0.1.md": first}))
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze"]), 0)
        return first, second

    def test_rows_are_judged_by_the_release_base_copy(self):
        first, _second = self.judge_repo()
        with open(os.path.join(self.rec, "freeze.json"), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["judge"], first)
        self.assertEqual(record["judge_source"], "the release base")
        self.assertIn("scripts/precut_review.py", record["judge_files"])
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        verdict, detail = rows["sbe-gate"]
        self.assertEqual(verdict, PR.PASS, detail)
        self.assertTrue(detail.startswith("judge-copy exit 0"), detail)
        self.assertEqual(PR.read_result(self.rec, "sbe-gate")[0], PR.PASS)
        for name in PR.SBE_ROWS:
            self.assertEqual(PR._row_mark(self.rec, name), first, name)
        manifest = PR._judged_manifest(self.rec)
        self.assertEqual((manifest["judge"], manifest["rows"]), (first, list(PR.SBE_ROWS)))
        # the judge's clean scanner ran, not the candidate's hit-reporting one
        verdict, detail = rows["scan-range"]
        self.assertEqual(verdict, PR.PASS, detail)
        self.assertIn("outgoing-scan PASS", detail)
        self.assertEqual(os.listdir(self.scratch), [])

    def test_judge_edit_needs_the_owners_review_record(self):
        first, second = self.judge_repo()
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        verdict, detail = rows["scanner-drift"]
        self.assertEqual(verdict, PR.NO_DATA, detail)
        self.assertIn("scripts/outgoing_scan.py", detail)
        self.assertIn(PR.JUDGE_REVIEW, detail)
        # the owner's hand
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["scanner-drift"],
                         (PR.PASS, '["scripts/outgoing_scan.py"]'))
        # a record for other blobs covers nothing
        with open(os.path.join(self.rec, PR.JUDGE_REVIEW), "w", encoding="utf-8") as handle:
            json.dump({"sha": second, "judge_files": {"scripts/outgoing_scan.py": "0" * 40}}, handle)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        verdict, detail = rows["scanner-drift"]
        self.assertEqual(verdict, PR.FAIL, detail)
        self.assertIn("without the owner's review record: scripts/outgoing_scan.py", detail)

    def seed_rows(self, verdict=PR.PASS, judged_by=None):
        for name in PR.SBE_ROWS:
            PR.record_result(self.rec, name, verdict, "seeded before the dispatch", judged_by)

    def test_stale_marked_rows_are_cleared_before_the_dispatch(self):
        """Rows left by an earlier dispatch already carry the judge's mark. A
        copy that writes a valid manifest and no rows must not get them read
        back as its answer: results/ is cleared before every dispatch."""
        module = (b"import json, os, sys\n"
                  b"rec = sys.argv[sys.argv.index('--dir') + 1]\n"
                  b"record = json.load(open(os.path.join(rec, 'freeze.json')))\n"
                  b"os.makedirs(os.path.join(rec, 'results'), exist_ok=True)\n"
                  b"json.dump({'mac': 'unsigned', 'sha': record['sha'], 'judge': record['judge'], 'rows': %r},\n"
                  b"          open(os.path.join(rec, 'results', 'judged.json'), 'w'))\n"
                  b"sys.exit(0)\n" % (list(PR.SBE_ROWS),))
        first, _second = self.judge_repo(module=module)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        self.seed_rows(judged_by=first)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        for name in PR.SBE_ROWS:
            verdict, detail = rows[name]
            self.assertEqual(verdict, PR.NO_DATA, (name, detail))
            self.assertIn("was not recorded by the judge copy at %s" % first[:12], detail)

    def test_a_crashing_judge_copy_is_no_answer_whatever_was_seeded(self):
        """The anchor's module raises before recording anything; five PASS
        rows seeded under the frozen sha must not come back as its answer."""
        first, _second = self.judge_repo(module=b"raise RuntimeError('the judge copy crashed')\n")
        # the fake judge carries no pins: the owner's record lets the dispatch past the pins gate
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        self.seed_rows()
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        for name in PR.SBE_ROWS:
            verdict, detail = rows[name]
            self.assertEqual(verdict, PR.NO_DATA, (name, detail))
            self.assertIn("the judge copy at %s did not answer: exit 1" % first[:12], detail)
            self.assertEqual(PR.read_result(self.rec, name)[0], PR.NO_DATA)
        self.assertIsNone(PR._judged_manifest(self.rec))

    def test_unmarked_rows_from_a_copy_that_exits_zero_are_refused(self):
        """A copy that writes PASS rows without the mark and no manifest, then
        exits 0, is no answer either: rows are bound to the dispatch."""
        module = (b"import json, os, sys\n"
                  b"rec = sys.argv[sys.argv.index('--dir') + 1]\n"
                  b"sha = json.load(open(os.path.join(rec, 'freeze.json')))['sha']\n"
                  b"os.makedirs(os.path.join(rec, 'results'), exist_ok=True)\n"
                  b"for name in %r:\n"
                  b"    json.dump({'check': name, 'sha': sha, 'verdict': 'PASS', 'detail': 'forged'},\n"
                  b"              open(os.path.join(rec, 'results', name + '.json'), 'w'))\n"
                  b"sys.exit(0)\n" % (list(PR.SBE_ROWS),))
        first, _second = self.judge_repo(module=module)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])
        self.assertIn("left no manifest bound to this dispatch", rows["sbe-gate"][1])
        # with a manifest copied from a real dispatch but no row marks
        module = module.replace(
            b"sys.exit(0)\n",
            b"judge = json.load(open(os.path.join(rec, 'freeze.json')))['judge']\n"
            b"json.dump({'mac': 'unsigned', 'sha': sha, 'judge': judge, 'rows': %r},\n"
            b"          open(os.path.join(rec, 'results', 'judged.json'), 'w'))\n"
            b"sys.exit(0)\n" % (list(PR.SBE_ROWS),))
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        shutil.rmtree(self.rec)
        first, _second = self.judge_repo(module=module)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])
        self.assertIn("was not recorded by the judge copy at %s" % first[:12], rows["sbe-gate"][1])

    def test_a_review_record_for_another_sha_covers_nothing_and_replace_clears_it(self):
        first, second = self.judge_repo()
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        with open(os.path.join(self.rec, PR.JUDGE_REVIEW), encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["sha"], second)
        record["sha"] = first
        with open(os.path.join(self.rec, PR.JUDGE_REVIEW), "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        verdict, detail = rows["scanner-drift"]
        self.assertEqual(verdict, PR.NO_DATA, detail)
        self.assertIn("is for %s, not the frozen %s" % (first, second[:12]), detail)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze", "--replace"]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.rec, PR.JUDGE_REVIEW)))

    def test_a_candidate_that_repins_the_judge_is_refused_until_reviewed(self):
        """The anchor's module pins another baseline landing commit; the
        candidate's own pins differ, so the dispatch is refused until the
        owner's record covers the candidate's module."""
        module = real_file("scripts/precut_review.py").replace(
            b'WAIVERS_BASELINE_LANDED = "', b'WAIVERS_BASELINE_LANDED = "0')
        self.assertNotEqual(module, real_file("scripts/precut_review.py"))
        first, _second = self.judge_repo(module=module)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["sbe-gate"][0], PR.NO_DATA, rows["sbe-gate"])
        self.assertIn("re-pins WAIVERS_BASELINE_LANDED against the judge at %s" % first[:12],
                      rows["sbe-gate"][1])
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["sbe-gate"][0], PR.PASS, rows["sbe-gate"])

    def test_bootstrap_judge_edits_are_no_data_without_the_record(self):
        rows = self.run_pass(base_fixture(), reviewed=False)
        verdict, detail = rows["scanner-drift"]
        self.assertEqual(verdict, PR.NO_DATA, detail)
        self.assertIn("no readable %s" % PR.JUDGE_REVIEW, detail)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = dict((name, (v, d)) for v, name, d in PR.sbe_pass(self.repo, self.rec))
        self.assertEqual(rows["scanner-drift"][0], PR.PASS, rows["scanner-drift"])
        self.assertIn("scripts/outgoing_scan.py", json.loads(rows["scanner-drift"][1]))

    def lint_tools(self):
        return dict((rel, real_file(rel)) for rel in LINT_TOOLS)

    def dispatch_runner(self, calls):
        """Runs everything for real and records each call's argv and stdin."""
        def runner(cmd, **kwargs):
            calls.append((list(cmd), kwargs.get("input"), kwargs.get("cwd")))
            return subprocess.run(cmd, **kwargs)
        return runner

    def test_a_stdlib_shadow_in_the_candidate_never_runs_inside_the_judge(self):
        """The forge of 2026-10-04, reproduced: the candidate commits a root
        datetime.py, a module sbe_score imports. Imported inside the judge's
        lint child it records that it ran, reads the dispatch argv with ps
        and leaves a detached writer that keeps rewriting every row as PASS
        and the manifest for three seconds after the judge's last write. No
        row may come back forged, and the shadow must never have run."""
        marks = os.path.join(self.tmp, "marks")
        os.makedirs(marks)
        first, second = self.judge_repo(first_extra=self.lint_tools(),
                                        second_extra={"datetime.py": SHADOW_DATETIME % (marks,)})
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        rows = PR.sbe_pass(self.repo, self.rec)
        if any(name.startswith("shadow-ran") for name in os.listdir(marks)):
            deadline = time.time() + 30
            while not os.path.exists(os.path.join(marks, "forger-done")) and time.time() < deadline:
                time.sleep(0.1)
        self.assertEqual(os.listdir(marks), [], "the candidate's datetime.py ran inside the judge")
        for verdict, name, detail in rows:
            self.assertNotIn("forged", detail, name)
        self.assertTrue(dict((n, d) for _v, n, d in rows)["sbe-gate"].startswith("judge-copy exit 0"))

    def test_an_inherited_pythonpath_never_reaches_a_judge_child(self):
        """PYTHONPATH names a directory holding sitecustomize.py, which any
        Python that honours PYTHONPATH imports at start. The dispatched judge
        and every child it starts (gate, lint, both scanners) are isolated,
        so it never runs."""
        marks = os.path.join(self.tmp, "marks")
        planted = os.path.join(self.tmp, "planted")
        os.makedirs(marks)
        os.makedirs(planted)
        _write_file(os.path.join(planted, "sitecustomize.py"),
                    "import os, sys\nopen(os.path.join(%r, 'ran-%%d' %% os.getpid()), 'w').close()\n"
                    % marks)
        self.judge_repo(first_extra=self.lint_tools())
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "accept-judge"]), 0)
        calls = []
        with mock.patch.dict(os.environ, {"PYTHONPATH": planted}):
            rows = PR.sbe_pass(self.repo, self.rec, self.dispatch_runner(calls))
        self.assertTrue(any("--as-judge" in cmd for cmd, _i, _c in calls))
        self.assertEqual(os.listdir(marks), [], "a judge child honoured PYTHONPATH")
        self.assertEqual(dict((n, v) for v, n, _d in rows)["sbe-gate"], PR.PASS)

    def test_the_lint_runs_outside_the_candidate_tree(self):
        """The gate reads the worktree from inside it; the lint names its
        tree through SBE_LINT_ROOT and runs from outside it."""
        calls = []
        record = self.dispatch_runner(calls)

        def runner(cmd, **kwargs):
            if cmd[:3] == ["git", "diff", "--name-only"]:
                return subprocess.CompletedProcess(cmd, 0, "ok.py\0", "")
            return record(cmd, **kwargs)
        rows = self.run_pass(base_fixture(**dict(self.lint_tools(), **{"ok.py": CLEAN_PY})), runner)
        self.assertIn(rows["silent-lint"][0], (PR.PASS, PR.FAIL), rows["silent-lint"])
        gate = [cwd for cmd, _i, cwd in calls if cmd[2:3] and cmd[2].endswith("sbe_gate.py")]
        lints = [cwd for cmd, _i, cwd in calls if "-c" in cmd]
        self.assertEqual(len(gate), 1)
        self.assertEqual(len(lints), 2)
        tree = os.path.realpath(gate[0])
        for cwd in lints:
            here = os.path.realpath(cwd)
            self.assertFalse(here == tree or here.startswith(tree + os.sep), (cwd, gate[0]))

    def test_the_dispatch_key_never_rides_on_argv(self):
        self.judge_repo()
        calls = []
        rows = PR.sbe_pass(self.repo, self.rec, self.dispatch_runner(calls))
        dispatch = [(cmd, given) for cmd, given, _c in calls if "--as-judge" in cmd]
        self.assertEqual(len(dispatch), 1)
        cmd, given = dispatch[0]
        self.assertRegex(given or "", r"\A[0-9a-f]{64}\n\Z")
        self.assertEqual(cmd[-1], "--as-judge")
        self.assertNotIn(given.strip(), " ".join(cmd))
        self.assertEqual(dict((n, v) for v, n, _d in rows)["sbe-gate"], PR.PASS)

    def test_the_dispatch_key_is_never_written_to_a_file(self):
        self.judge_repo()
        calls = []
        PR.sbe_pass(self.repo, self.rec, self.dispatch_runner(calls))
        key = [given for cmd, given, _c in calls if "--as-judge" in cmd][0].strip()
        for folder, _dirs, names in os.walk(self.rec):
            for name in names:
                with open(os.path.join(folder, name), "rb") as handle:
                    self.assertNotIn(key.encode("ascii"), handle.read(), name)

    def test_the_judge_side_takes_its_key_from_stdin_only(self):
        """A key on argv is refused by the judge's own parser, and an empty
        or malformed stdin is no key, on a frozen bootstrap tree where the
        rows would otherwise run and the copy would exit 0."""
        make_repo(self.repo, base_fixture())
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze"]), 0)
        argv = ["--dir", self.rec, "--root", self.repo, "sbe", "--as-judge"]
        self.assertEqual(PR.main(argv + ["a" * 64]), 2)
        for given in ("", "not-hex\n"):
            with mock.patch.object(sys, "stdin", io.StringIO(given)):
                self.assertEqual(PR.main(argv), 2, given)
        self.assertFalse(os.path.exists(os.path.join(self.rec, "results")))

    def test_a_row_rewritten_before_the_judge_exits_is_refused(self):
        """Something other than the judge rewrites a row while the judge is
        still running (before its exit, so the time guard holds): the row no
        longer matches the judge's signature, and no row is returned."""
        module = real_file("scripts/precut_review.py").replace(
            JUDGE_LAST_WRITE, JUDGE_LAST_WRITE +
            b"        record_result(rec_dir, 'scan-range', PASS, 'forged inside the window', judge)\n")
        self.assertNotEqual(module, real_file("scripts/precut_review.py"))
        self.judge_repo(module=module)
        rows = PR.sbe_pass(self.repo, self.rec)
        for verdict, name, detail in rows:
            self.assertEqual(verdict, PR.NO_DATA, (name, detail))
            self.assertIn("do not carry the judge copy's signature", detail)

    def test_a_result_modified_after_the_judge_exits_is_refused(self):
        """A row whose content is the judge's own, signed and unchanged, but
        whose modification time is after the judge exited: only a process
        outliving the judge can have written it."""
        module = real_file("scripts/precut_review.py").replace(
            JUDGE_LAST_WRITE, JUDGE_LAST_WRITE +
            b"        later = time.time() + 3600\n"
            b"        os.utime(os.path.join(results, 'waivers.json'), (later, later))\n")
        self.assertNotEqual(module, real_file("scripts/precut_review.py"))
        self.judge_repo(module=module)
        rows = PR.sbe_pass(self.repo, self.rec)
        for verdict, name, detail in rows:
            self.assertEqual(verdict, PR.NO_DATA, (name, detail))
            self.assertIn("waivers.json was modified after the judge copy exited", detail)

    def test_main_sbe_exit_code(self):
        make_repo(self.repo, base_fixture())
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "freeze"]), 0)
        self.assertEqual(PR.main(["--dir", self.rec, "--root", self.repo, "sbe"]), 2)
        self.assertEqual(PR.read_result(self.rec, "sbe-gate")[0], PR.PASS)
        self.assertEqual(PR.read_result(self.rec, "silent-lint")[0], PR.NO_DATA)


#: The judge's last write in --as-judge mode; fixtures inject after it.
JUDGE_LAST_WRITE = b"        os.replace(temp, os.path.join(results, JUDGED_MANIFEST))\n"

#: The writer a shadow module leaves behind: once the judge's manifest
#: appears it rewrites every row as PASS, marked with the judge, and the
#: manifest with the nonce it read from the dispatch argv, for three seconds.
FORGER = r"""
import json, os, sys, time
rec, nonce, marks = sys.argv[1:4]
with open(os.path.join(rec, "freeze.json")) as fh:
    frozen = json.load(fh)
res = os.path.join(rec, "results")
rows = ["sbe-gate", "silent-lint", "waivers", "scanner-drift", "scan-range"]

def put(name, data):
    tmp = os.path.join(res, ".forge-" + name)
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, os.path.join(res, name))

stop = None
deadline = time.time() + 25
while time.time() < deadline:
    if stop is None and os.path.exists(os.path.join(res, "judged.json")):
        stop = time.time() + 3
    if stop is not None:
        if time.time() > stop:
            break
        try:
            for row in rows:
                put(row + ".json", {"check": row, "sha": frozen["sha"], "verdict": "PASS",
                                    "detail": "forged", "judged_by": frozen["judge"]})
            put("judged.json", {"nonce": nonce, "sha": frozen["sha"], "judge": frozen["judge"],
                                "rows": rows})
        except OSError:
            pass
    time.sleep(0.002)
open(os.path.join(marks, "forger-done"), "w").close()
"""

#: A candidate root datetime.py: records that it ran, reads the dispatch
#: argv with ps, leaves FORGER detached, then becomes the real datetime so
#: the lint it rode in on finishes normally.
SHADOW_DATETIME = (b"import os, subprocess, sys\n"
                   b"_marks = %r\n"
                   b"open(os.path.join(_marks, 'shadow-ran-%%d' %% os.getpid()), 'w').close()\n"
                   b"_rec = _nonce = None\n"
                   b"try:\n"
                   b"    _ps = subprocess.run(['ps', '-axww', '-o', 'args='], capture_output=True,\n"
                   b"                         text=True).stdout\n"
                   b"except OSError:\n"
                   b"    _ps = ''\n"
                   b"for _line in _ps.splitlines():\n"
                   b"    _p = _line.split()\n"
                   b"    if '--as-judge' in _p and '--dir' in _p:\n"
                   b"        _rec = _p[_p.index('--dir') + 1]\n"
                   b"        _i = _p.index('--as-judge') + 1\n"
                   b"        _nonce = _p[_i] if _i < len(_p) else ''\n"
                   b"if _rec:\n"
                   b"    subprocess.Popen([sys.executable, '-c', " + repr(FORGER).encode("ascii") +
                   b", _rec, _nonce, _marks],\n"
                   b"                     start_new_session=True, stdin=subprocess.DEVNULL,\n"
                   b"                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
                   b"                     close_fds=True)\n"
                   b"_here = os.path.dirname(os.path.abspath(__file__))\n"
                   b"sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != _here]\n"
                   b"del sys.modules['datetime']\n"
                   b"import datetime as _real\n"
                   b"sys.modules['datetime'] = _real\n")


def reprable(value):
    try:
        return repr(value)
    except Exception:
        return type(value).__name__


if __name__ == "__main__":
    unittest.main()
