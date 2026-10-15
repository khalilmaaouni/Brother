"""FR1.b: export_tree_cache and the cut path's use of it. Every case drives a real entry point (lease_into, release,
main, cut_preflight.export_tree_builder) against a throwaway git repository as the source root and a counting build
that leaves the shape build_orphan_commit leaves (scripts/required_fast.sh in a one commit repository). The real
exporter never runs. Every cache folder lives under a temp dir named by BROTHER_SCRATCH, never under the home folder.
Runs on the public export tree under an empty HOME: nothing here reads docs/plan or calls ps."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_preflight as P  # noqa: E402
import export_tree_cache as E  # noqa: E402
import linux_canary as L  # noqa: E402
import test_linux_canary as TLC  # noqa: E402  (its FakeGh and FakeGit answer run_canary up to the build)

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    r = subprocess.run(["git"] + IDENT + list(args), cwd=cwd, env=CLEAN_ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def entries(base):
    """The published entries (16 hex names) under a cache folder."""
    if not os.path.isdir(base):
        return []
    return sorted(n for n in os.listdir(base) if re.fullmatch(r"[0-9a-f]{16}", n))


class Builder(object):
    """build(dest): counts calls and leaves a one commit repository holding scripts/required_fast.sh. unique makes
    every call a different tree; barrier holds each call until both threads are building."""

    def __init__(self, unique=False, barrier=None, boom=None):
        self.calls, self.unique, self.barrier, self.boom = 0, unique, barrier, boom
        self._lock = threading.Lock()

    def __call__(self, dest):
        with self._lock:
            self.calls += 1
        if self.barrier is not None:
            self.barrier.wait(10)
        if self.boom:
            raise RuntimeError(self.boom)
        os.makedirs(os.path.join(dest, "scripts"), exist_ok=True)
        with open(os.path.join(dest, "scripts", "required_fast.sh"), "w") as fh:
            fh.write("echo %s\n" % (uuid.uuid4().hex if self.unique else "fixed"))
        git(dest, "init", "-q")
        git(dest, "add", "-A")
        git(dest, "commit", "-qm", "orphan")


class Fixture(object):
    def setUp(self):
        self.box = tempfile.mkdtemp(prefix="test-export-tree-cache-")
        self.addCleanup(shutil.rmtree, self.box, True)
        patch = mock.patch.dict(os.environ, {"BROTHER_SCRATCH": os.path.join(self.box, "scratch")})
        patch.start()
        self.addCleanup(patch.stop)
        self.base = E.cache_root()
        self.root = os.path.join(self.box, "root")
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(os.path.join(self.root, "docs", "plan"))
        self.write(self.root, "scripts/export_public.py", "x = 1\n")
        self.write(self.root, "docs/plan/EXPORT-ALLOWLIST.txt", "# header\nscripts\n")
        self.write(self.root, "README.md", "one\n")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        # key files outside the repository: changing their bytes changes ONE key field and nothing else
        self.exporter = os.path.join(self.box, "exporter.py")
        self.allow = os.path.join(self.box, "allowlist.txt")
        self.write(self.box, "exporter.py", "e = 1\n")
        self.write(self.box, "allowlist.txt", "scripts\n")

    def write(self, folder, rel, text):
        with open(os.path.join(folder, rel), "w") as fh:
            fh.write(text)

    def dest(self):
        return tempfile.mkdtemp(dir=self.box, prefix="consumer-")

    def lease(self, build, **kw):
        kw.setdefault("exporter", self.exporter)
        kw.setdefault("allowlist", self.allow)
        return E.lease_into(self.dest(), self.root, build=build, **kw)

    def entry_dir(self):
        names = entries(self.base)
        self.assertEqual(len(names), 1, names)
        return os.path.join(self.base, names[0])


class TheCacheBuildsOnceAndHandsEveryoneTheirOwnTree(Fixture, unittest.TestCase):
    def test_a_second_lease_does_not_rebuild(self):
        b = Builder()
        first, why1 = self.lease(b)
        second, why2 = self.lease(b)
        self.assertEqual((why1, why2), ("", ""))
        self.assertEqual(b.calls, 1)
        self.assertEqual(first.export_tree_id, second.export_tree_id)
        self.assertRegex(first.export_tree_id, r"^[0-9a-f]{40}$")
        self.assertEqual(len(entries(self.base)), 1)

    def test_each_consumer_gets_its_own_directory(self):
        b = Builder()
        one, _ = self.lease(b)
        two, _ = self.lease(b)
        self.assertNotEqual(one.path, two.path)
        for lease in (one, two):
            self.assertTrue(os.path.isfile(os.path.join(lease.path, P.EXPORT_TREE_MARKER)))
            self.assertTrue(os.path.isfile(os.path.join(lease.path, ".git")), "a worktree has a .git file")
        orphan = os.path.join(self.entry_dir(), "orphan")
        self.assertNotEqual(os.path.realpath(one.path), os.path.realpath(orphan))
        self.write(one.path, "scripts/consumer-wrote-this", "x")
        self.assertFalse(os.path.exists(os.path.join(two.path, "scripts", "consumer-wrote-this")))
        self.assertEqual(git(orphan, "status", "--porcelain"), "", "a consumer's write reached the cached entry")
        self.assertEqual(git(one.path, "rev-parse", "HEAD^{tree}"), one.export_tree_id)

    def test_a_leased_path_is_the_dest_the_consumer_made(self):
        dest = self.dest()
        lease, why = E.lease_into(dest, self.root, build=Builder(), exporter=self.exporter, allowlist=self.allow)
        self.assertEqual((lease.path, why), (dest, ""))


class TheKeyNamesTheBytesTheTreeCameFrom(Fixture, unittest.TestCase):
    def test_a_changed_exporter_rebuilds(self):
        b = Builder()
        self.lease(b)
        self.write(self.box, "exporter.py", "e = 2\n")
        self.lease(b)
        self.assertEqual((b.calls, len(entries(self.base))), (2, 2))

    def test_a_changed_allowlist_rebuilds(self):
        b = Builder()
        self.lease(b)
        self.write(self.box, "allowlist.txt", "scripts\ndocs\n")
        self.lease(b)
        self.assertEqual((b.calls, len(entries(self.base))), (2, 2))

    def test_a_changed_source_tree_rebuilds(self):
        b = Builder()
        self.lease(b)
        self.write(self.root, "README.md", "two\n")
        git(self.root, "commit", "-qam", "change")
        self.lease(b)
        self.assertEqual((b.calls, len(entries(self.base))), (2, 2))

    def test_the_unchanged_key_reuses_across_a_new_commit_of_the_same_tree(self):
        b = Builder()
        self.lease(b)
        git(self.root, "commit", "-q", "--allow-empty", "-m", "same tree, new commit")
        self.lease(b)
        self.assertEqual(b.calls, 1)

    def test_a_dirty_source_is_never_cached(self):
        b = Builder()
        self.write(self.root, "README.md", "edited, not committed\n")
        dest = self.dest()
        lease, why = E.lease_into(dest, self.root, build=b, exporter=self.exporter, allowlist=self.allow)
        self.assertIsNone(lease)
        self.assertIn("dirty", why)
        self.assertEqual((b.calls, entries(self.base), os.listdir(dest)), (0, [], []))

    def test_hostile_roots_and_files_return_a_reason_not_a_raise(self):
        plain = os.path.join(self.box, "not-a-repo")
        os.makedirs(plain)
        for root in ("", None, 7, os.path.join(self.box, "missing"), plain):
            lease, why = E.lease_into(self.dest(), root, build=Builder())
            self.assertIsNone(lease, root)
            self.assertTrue(why and isinstance(why, str), root)
        empty = os.path.join(self.box, "empty-allowlist.txt")
        self.write(self.box, "empty-allowlist.txt", "# only a comment\n\n")
        lease, why = E.lease_into(self.dest(), self.root, build=Builder(), exporter=self.exporter, allowlist=empty)
        self.assertIsNone(lease)
        self.assertIn("allowlist", why)
        lease, why = self.lease(Builder(), allowlist=os.path.join(self.box, "no-such-file"))
        self.assertIsNone(lease)
        self.assertIn("could not be read", why)

    def test_a_dest_that_is_not_empty_is_refused_and_left_alone(self):
        dest = self.dest()
        self.write(dest, "keep.txt", "mine")
        lease, why = E.lease_into(dest, self.root, build=Builder(), exporter=self.exporter, allowlist=self.allow)
        self.assertIsNone(lease)
        self.assertEqual(os.listdir(dest), ["keep.txt"])
        self.assertIsNone(E.lease_into("", self.root)[0])
        self.assertIsNone(E.lease_into(None, self.root)[0])


class ABadEntryIsRebuiltNeverHandedOut(Fixture, unittest.TestCase):
    def leased_once(self):
        b = Builder(unique=True)
        first, _ = self.lease(b)
        self.assertEqual(b.calls, 1)
        return b, first

    def test_a_mutated_cache_entry_is_rebuilt(self):
        b, first = self.leased_once()
        self.write(os.path.join(self.entry_dir(), "orphan"), "scripts/injected.txt", "tampered")
        second, why = self.lease(b)
        self.assertEqual(why, "")
        self.assertEqual(b.calls, 2)
        self.assertFalse(os.path.exists(os.path.join(second.path, "scripts", "injected.txt")))
        self.assertEqual(git(os.path.join(self.entry_dir(), "orphan"), "status", "--porcelain"), "")

    def test_a_corrupt_meta_is_rebuilt(self):
        b, first = self.leased_once()
        with open(os.path.join(self.entry_dir(), "meta.json"), "w") as fh:
            fh.write('{"key_fields": {"source_t')   # truncated
        second, why = self.lease(b)
        self.assertEqual((why, b.calls), ("", 2))
        self.assertEqual(git(second.path, "rev-parse", "HEAD^{tree}"), second.export_tree_id)

    def test_a_meta_that_names_another_tree_is_rebuilt(self):
        b, first = self.leased_once()
        meta = os.path.join(self.entry_dir(), "meta.json")
        with open(meta) as fh:
            data = json.load(fh)
        data["export_tree_id"] = "0" * 40
        with open(meta, "w") as fh:
            json.dump(data, fh)
        second, why = self.lease(b)
        self.assertEqual((why, b.calls), ("", 2))
        self.assertNotEqual(second.export_tree_id, "0" * 40)

    def test_a_meta_that_names_another_key_is_rebuilt(self):
        b, first = self.leased_once()
        meta = os.path.join(self.entry_dir(), "meta.json")
        with open(meta) as fh:
            data = json.load(fh)
        data["key_fields"]["exporter_sha256"] = "f" * 64
        with open(meta, "w") as fh:
            json.dump(data, fh)
        second, why = self.lease(b)
        self.assertEqual((why, b.calls), ("", 2))

    def test_an_entry_without_its_marker_file_is_rebuilt(self):
        """The marker is removed from the commit and the meta follows it, so the tree id and the status both agree
        and only the marker rule can refuse the entry."""
        b, first = self.leased_once()
        entry = self.entry_dir()
        orphan = os.path.join(entry, "orphan")
        git(orphan, "rm", "-q", P.EXPORT_TREE_MARKER)
        git(orphan, "commit", "-qm", "marker removed")
        meta = os.path.join(entry, "meta.json")
        with open(meta) as fh:
            data = json.load(fh)
        data["export_tree_id"] = git(orphan, "rev-parse", "HEAD^{tree}")
        with open(meta, "w") as fh:
            json.dump(data, fh)
        self.assertEqual(git(orphan, "status", "--porcelain"), "")
        second, why = self.lease(b)
        self.assertEqual((why, b.calls), ("", 2))
        self.assertTrue(os.path.isfile(os.path.join(second.path, P.EXPORT_TREE_MARKER)))

    def test_a_failing_build_leaves_no_entry(self):
        dest = self.dest()
        lease, why = E.lease_into(dest, self.root, build=Builder(boom="exporter exploded"),
                                  exporter=self.exporter, allowlist=self.allow)
        self.assertIsNone(lease)
        self.assertIn("exporter exploded", why)
        self.assertEqual(entries(self.base), [])
        self.assertEqual([n for n in os.listdir(self.base) if ".tmp-" in n], [])
        self.assertEqual(os.listdir(dest), [])

    def test_a_build_with_no_marker_leaves_no_entry(self):
        def no_marker(dest):
            git(dest, "init", "-q")
        lease, why = self.lease(no_marker)
        self.assertIsNone(lease)
        self.assertEqual(entries(self.base), [])


class TwoConsumersAtOnce(Fixture, unittest.TestCase):
    def test_two_threads_leasing_leave_one_entry(self):
        for attempt in range(10):
            shutil.rmtree(self.base, ignore_errors=True)
            b = Builder(unique=True, barrier=threading.Barrier(2))
            out, errs = [], []

            def go():
                try:
                    out.append(self.lease(b))
                except Exception as exc:   # sbe: allow-silent the assertion below reports it
                    errs.append(exc)
            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            self.assertEqual(errs, [], attempt)
            self.assertEqual(b.calls, 2, "both threads must have built for the race to exist")
            self.assertEqual([why for _l, why in out], ["", ""], attempt)
            names = entries(self.base)
            self.assertEqual(len(names), 1, (attempt, names))
            self.assertEqual([n for n in os.listdir(self.base) if ".tmp-" in n], [], attempt)
            with open(os.path.join(self.base, names[0], "meta.json")) as fh:
                final = json.load(fh)["export_tree_id"]
            for lease, _why in out:
                self.assertEqual(lease.export_tree_id, final, attempt)
                self.assertEqual(git(lease.path, "rev-parse", "HEAD^{tree}"), final, attempt)
                self.assertEqual(git(lease.path, "status", "--porcelain"), "", attempt)


class TheLeaseLock(Fixture, unittest.TestCase):
    def test_a_live_lock_holds_the_lease_and_a_dead_one_is_replaced(self):
        b = Builder()
        self.lease(b)
        fields, key = E.cache_key(self.root, self.exporter, self.allow)
        lock = os.path.join(self.base, key[:16] + ".lock")
        with open(lock, "w") as fh:
            fh.write(str(os.getpid()))
        dest = self.dest()
        with mock.patch.object(E, "LOCK_WAIT_S", 0.3):
            lease, why = E.lease_into(dest, self.root, build=b, exporter=self.exporter, allowlist=self.allow)
        self.assertIsNone(lease)
        self.assertIn("lock", why)
        self.assertEqual(os.listdir(dest), [])
        self.assertTrue(os.path.exists(lock), "a live owner's lock is never taken from it")
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        with open(lock, "w") as fh:
            fh.write(str(gone.pid))
        lease, why = E.lease_into(dest, self.root, build=b, exporter=self.exporter, allowlist=self.allow)
        self.assertEqual(why, "")
        self.assertFalse(os.path.exists(lock), "the lock is released after the worktree add")
        self.assertEqual(b.calls, 1)


class ReleaseTakesTheWorktreeAndItsRecord(Fixture, unittest.TestCase):
    def test_release_removes_the_worktree_and_its_record(self):
        lease, _ = self.lease(Builder())
        other, _ = self.lease(Builder())
        orphan = os.path.join(self.entry_dir(), "orphan")
        self.assertIn(os.path.basename(lease.path), git(orphan, "worktree", "list", "--porcelain"))
        self.assertEqual(E.release(lease), "")
        self.assertFalse(os.path.exists(lease.path))
        listed = git(orphan, "worktree", "list", "--porcelain")
        self.assertNotIn(os.path.basename(lease.path), listed)
        self.assertIn(os.path.basename(other.path), listed, "the other consumer's lease stays")
        self.assertTrue(os.path.isdir(other.path))

    def test_release_never_raises(self):
        gone = E.Lease(os.path.join(self.box, "never-leased"), "0" * 40, "k")
        self.assertIsInstance(E.release(gone), str)
        plain = self.dest()
        self.assertIsInstance(E.release(E.Lease(plain, "0" * 40, "k")), str)
        self.assertFalse(os.path.exists(plain))
        self.assertIsInstance(E.release(None), str)

    def test_a_consumer_that_deleted_its_tree_without_release_does_not_block_the_next_lease(self):
        b = Builder()
        lease, _ = self.lease(b)
        shutil.rmtree(lease.path)
        again, why = self.lease(b)
        self.assertEqual((why, b.calls), ("", 1))
        orphan = os.path.join(self.entry_dir(), "orphan")
        self.assertNotIn(os.path.basename(lease.path), git(orphan, "worktree", "list", "--porcelain"))


class TheCutPathBuilderUsesTheCacheAndTheDefaultDoesNot(Fixture, unittest.TestCase):
    def fake_exporter(self):
        calls = []
        fake = mock.Mock()
        fake.ROOT = self.root
        fake.DEFAULT_ALLOWLIST = os.path.join(self.root, "docs", "plan", "EXPORT-ALLOWLIST.txt")
        fake.__file__ = os.path.join(self.root, "scripts", "export_public.py")
        fake.load_allowlist.return_value = []

        def build_orphan_commit(dest, allow, root):
            calls.append(dest)
            Builder()(dest)
        fake.build_orphan_commit.side_effect = build_orphan_commit
        return fake, calls

    def test_the_cache_flag_routes_the_builder_and_the_default_does_not(self):
        fake, calls = self.fake_exporter()
        with mock.patch.dict(sys.modules, {"export_public": fake}):
            for _ in range(2):
                P.export_tree_builder(self.root, cache=True)(self.dest())
            self.assertEqual(len(calls), 1, "cache=True builds once for two consumers")
            self.assertEqual(len(entries(self.base)), 1)
            del calls[:]
            for _ in range(2):
                P.export_tree_builder(self.root)(self.dest())
            self.assertEqual(len(calls), 2, "the default builder builds per call and never touches the cache")
            self.assertEqual(len(entries(self.base)), 1)

    def test_a_dirty_root_falls_through_to_the_plain_build(self):
        fake, calls = self.fake_exporter()
        self.write(self.root, "README.md", "dirty\n")
        with mock.patch.dict(sys.modules, {"export_public": fake}):
            dest = self.dest()
            P.export_tree_builder(self.root, cache=True)(dest)
        self.assertEqual(calls, [dest])
        self.assertTrue(os.path.isfile(os.path.join(dest, P.EXPORT_TREE_MARKER)))
        self.assertEqual(entries(self.base), [])

    def test_the_id_and_the_virgin_gate_builders_ask_for_the_cache(self):
        seen = []
        real = P.export_tree_builder

        def spy(root, cache=False):
            seen.append(cache)
            return real(root, cache)
        fake, calls = self.fake_exporter()
        with mock.patch.dict(sys.modules, {"export_public": fake}), \
                mock.patch.object(P, "export_tree_builder", side_effect=spy):
            tree_id, why = P.export_tree_id(self.root, build=None)
            verdict = P.check_virgin_gate(self.root, runner=None, build=None,
                                          keep=os.path.join(self.box, "virgin.txt"))[0]
        self.assertEqual(seen, [True, True])
        self.assertEqual((why, len(calls), len(tree_id)), ("", 1, 40), "the second consumer was served by the cache")
        self.assertEqual(verdict, P.NODATA, "the fixture's required_fast.sh prints no summary line")


class TheCanaryAsksForTheCache(Fixture, unittest.TestCase):
    def test_run_canary_builds_through_the_cached_builder(self):
        seen = []

        def spy(root, cache=False):
            seen.append(cache)

            def stop(dest):
                raise RuntimeError("stop before any push")
            return stop
        with mock.patch.object(P, "export_tree_builder", side_effect=spy), contextlib.redirect_stdout(io.StringIO()):
            verdict, detail, _path = L.run_canary("/fake/root", TLC.V, gh=TLC.FakeGh(), git=TLC.FakeGit(),
                                                  directory=self.box)
        self.assertEqual(seen, [True])
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("stop before any push", detail)


class TheCacheIsBounded(Fixture, unittest.TestCase):
    def test_the_cache_sits_under_the_scratch_root_and_is_pruned(self):
        scratch = os.path.join(self.box, "scratch")
        self.assertEqual(E.cache_root(), os.path.join(scratch, "export-tree-cache"))
        self.assertEqual(E.cache_root(os.path.join(self.box, "elsewhere")), os.path.join(self.box, "elsewhere"))
        b = Builder()
        for i in range(10):
            self.write(self.root, "README.md", "revision %d\n" % i)
            git(self.root, "commit", "-qam", "r%d" % i)
            lease, why = self.lease(b)
            self.assertEqual(why, "")
            self.assertLessEqual(len(entries(self.base)), E.MAX_ENTRIES)
        self.assertEqual((b.calls, len(entries(self.base))), (10, E.MAX_ENTRIES))
        self.assertTrue(os.path.isfile(os.path.join(lease.path, P.EXPORT_TREE_MARKER)), "the newest entry is kept")
        # age: every entry two days old goes on the next publish, a stale tmp folder with them
        old = os.path.getmtime(self.base) - 2 * E.MAX_AGE_S
        stale = os.path.join(self.base, "0123456789abcdef.tmp-1-deadbeef")
        os.makedirs(stale)
        for name in os.listdir(self.base):
            os.utime(os.path.join(self.base, name), (old, old))
        self.write(self.root, "README.md", "after the age\n")
        git(self.root, "commit", "-qam", "late")
        self.lease(b)
        self.assertEqual(len(entries(self.base)), 1)
        self.assertFalse(os.path.exists(stale))

    def test_prune_touches_only_the_cache_folder(self):
        outside = os.path.join(self.box, "scratch", "canary-build")
        os.makedirs(outside)
        old = os.path.getmtime(outside) - 5 * E.MAX_AGE_S
        os.utime(outside, (old, old))
        for i in range(5):
            self.write(self.root, "README.md", "r%d\n" % i)
            git(self.root, "commit", "-qam", "r%d" % i)
            self.lease(Builder())
        self.assertTrue(os.path.isdir(outside))


class TheCommandLine(Fixture, unittest.TestCase):
    def run_main(self, argv, build):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = E.main(argv, build=build)
        return code, out.getvalue().strip()

    def test_the_cli_prints_the_id_line_or_no_data(self):
        code, said = self.run_main(["--root", self.root, "--id"], Builder())
        self.assertEqual(code, 0)
        self.assertRegex(said, r"^EXPORT-TREE: [0-9a-f]{40}$")
        before = sorted(os.listdir(os.path.join(self.box, "scratch")))
        code2, said2 = self.run_main(["--root", self.root, "--id"], Builder(boom="no build"))
        self.assertEqual((code2, said2), (0, said), "the second call is served by the cache")
        self.write(self.root, "README.md", "dirty\n")
        code3, said3 = self.run_main(["--root", self.root, "--id"], Builder())
        self.assertEqual(code3, 2)
        self.assertTrue(said3.startswith("NO-DATA: "), said3)
        self.assertEqual(sorted(os.listdir(os.path.join(self.box, "scratch"))), before,
                         "the command left no leased tree behind")

    def test_the_cli_as_a_process_reports_no_data_for_a_missing_root(self):
        env = dict(CLEAN_ENV, BROTHER_SCRATCH=os.path.join(self.box, "scratch"))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "export_tree_cache.py"),
                            "--root", os.path.join(self.box, "missing"), "--id"],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("NO-DATA: "), r.stdout)


if __name__ == "__main__":
    unittest.main()
