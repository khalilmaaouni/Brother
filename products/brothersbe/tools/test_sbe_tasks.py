#!/usr/bin/env python3
"""Fixtures for `sbe task`. Run: python3 tools/test_sbe_tasks.py

Every test here builds a real git repository in a temporary directory and runs
the real command against it. Nothing is mocked, because the defect this control
exists for lives exactly at the seam between what a writer declared and what
the tree actually shows, and a mocked diff would test the mock.

The point being pinned, in one sentence: a Bash-made write outside declared
scope is detected AFTER THE FACT by reading the diff, so the fixtures write
files with plain open() calls, exactly the way a shell edit lands, and never
through any tool the fence hook could have seen.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import shutil
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class TaskFixture(unittest.TestCase):
    """A fresh repository per test, with one base commit already in it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        write(self.repo, "src/owned.py", "x = 1\n")
        write(self.repo, "docs/other.md", "prose\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE, "task"] + list(argv) +
                             ["--cwd", self.repo], capture_output=True, text=True)
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses any
        # such function sitting outside a check registry.
        return out.returncode, out.stdout + out.stderr, out.stderr

    def open_task(self, task_id="w1", agent="alpha", role="writer", owns=("src/owned.py",),
                  base=None, change=None, extra=()):
        argv = ["open", "--id", task_id, "--agent", agent, "--role", role,
                "--base", base or self.base, "--verify", "python3 -m pytest"]
        if change is not None:
            argv += ["--change", change]
        for p in owns:
            argv += ["--owns", p]
        argv += list(extra)
        return self.sbe(*argv)

    def registry(self):
        with io.open(os.path.join(self.repo, ".sbe", "tasks.json"),
                     encoding="utf-8") as fh:
            return json.load(fh)


class TestRoundTrip(TaskFixture):
    def test_open_list_close_round_trip_clean_diff_closes_clean(self):
        code, text, _ = self.open_task()
        self.assertEqual(code, 0, text)
        code, text, _ = self.sbe("list")
        self.assertEqual(code, 0, text)
        self.assertIn("w1", text)
        self.assertIn("alpha", text)
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertEqual(code, 0, text)
        self.assertIn("PASS", text)
        self.assertIn("Closed clean", text)
        # THE DEFECT THIS CLOSES (P4): an ordinary close used to say nothing
        # at all about the decision record. This repository holds no
        # `.sbe/decisions/` store, so the close must say so, never a bare
        # silence, and never move its own exit code doing it.
        self.assertIn("NO-DATA", text,
                      "an ordinary close with zero decision packages must name the absence")
        self.assertIn("decision package", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "closed", record)
        self.assertTrue(record["closedAt"], "a closed task must carry its closedAt")
        self.assertNotIn("forced", record, "a clean close must not look forced")


class TestDecisionPackageNote(TaskFixture):
    """Tests driven backwards, per the P4 brief: a close with zero decision
    packages prints the NO-DATA line (already pinned above on the plain
    round trip); a close with at least one package does not."""

    def _write_a_real_package(self):
        """A real package, written the same way `record_forced_close` writes
        one: `build_package`/`write_package`, never a hand-typed fixture file
        `list_packages` would have to guess the shape of. Run in a SEPARATE
        process: `build_package` -> `deciding_code` -> `registries()` walks
        every `.py` under `tools/` with `SourceFileLoader`, which re-registers
        `sbe_fence_hook` in `sys.modules` under a fresh module object; calling
        it in this test process would desync `tasks.paths_overlap`'s already
        -bound reference from a later plain `import sbe_fence_hook` elsewhere
        in this same suite."""
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from brothersbe import decisions as decisions_mod\n"
            "package = decisions_mod.build_package(%r, {\n"
            "    'kind': 'gate', 'check': 'unit-tests', 'verdict': 'FAIL',\n"
            "    'verdictLine': 'unit-tests FAIL boom', 'otherLines': [], 'dossier': None})\n"
            "decisions_mod.write_package(%r, package)\n"
        ) % (os.path.join(ROOT, "src"), self.repo, self.repo)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError("writing the fixture package failed: %s" % out.stderr)

    def test_a_clean_close_with_a_package_already_on_record_prints_no_no_data_line(self):
        self.open_task(owns=("src/owned.py", ".sbe/decisions", ".sbe/decisions.lock"))
        write(self.repo, "src/owned.py", "x = 2\n")
        self._write_a_real_package()
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertEqual(code, 0, text)
        self.assertIn("PASS", text)
        self.assertNotIn("NO-DATA: no decision package is recorded", text,
                         "a close must not claim the record is empty when a package exists")

    def test_a_forced_close_still_writes_its_own_package_and_is_unaffected_by_this_note(self):
        self.open_task()
        write(self.repo, "docs/other.md", "sneaky\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1", "--force", "--who", "khalil", "--why", "ship it")
        self.assertEqual(code, 0, text)
        self.assertIn("FORCED", text)
        self.assertIn("decision package written", text,
                      "a forced close's own unconditional package write must still happen")


class TestTheDefect(TaskFixture):
    def test_a_bash_made_edit_outside_owned_paths_is_named_at_close(self):
        """THE DEFECT THIS CONTROL EXISTS FOR. The write below goes through
        plain open(), the way a shell edit lands, past every pre-write hook.
        The close reads the diff and names it anyway."""
        self.open_task()
        write(self.repo, "src/owned.py", "x = 2\n")
        write(self.repo, "docs/other.md", "sneaky\n")  # plain open(), no tool
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertNotEqual(code, 0, "an out-of-scope write must not close clean")
        self.assertIn("VIOLATION", text)
        self.assertIn("docs/other.md", text, "the violation must be named by path")
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "open", "the task must stay open on FAIL")

    def test_an_uncommitted_edit_counts_via_the_porcelain_path(self):
        self.open_task()
        write(self.repo, "docs/other.md", "uncommitted sneak\n")  # never committed
        code, text, _ = self.sbe("close", "w1")
        self.assertNotEqual(code, 0,
                            "an uncommitted out-of-scope edit must count; the porcelain "
                            "union exists for exactly this")
        self.assertIn("docs/other.md", text)

    def test_a_rename_counts_both_sides(self):
        self.open_task(owns=("src/owned.py", "src/renamed.py"))
        git(self.repo, "mv", "docs/other.md", "docs/moved.md")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertNotEqual(code, 0, text)
        self.assertIn("docs/other.md", text, "the old side of a rename must be named")
        self.assertIn("docs/moved.md", text, "the new side of a rename must be named")


class TestOverlap(TaskFixture):
    def test_a_second_open_with_an_overlapping_owned_path_is_refused(self):
        self.open_task(task_id="first", owns=("src/",))
        code, text, _ = self.open_task(task_id="second", owns=("src/owned.py",))
        self.assertNotEqual(code, 0, "two open writers over one path is the collision "
                                     "this registry exists to refuse")
        self.assertIn("first", text, "the refusal must name the colliding task id")
        code, text, _ = self.sbe("list")
        self.assertNotIn("second", text, "a refused open must not be recorded")

    def test_check_catches_a_collision_injected_directly_into_the_json(self):
        """The registry is one JSON file anybody can edit. `check` is the scan
        that catches what `open` never saw."""
        self.open_task(task_id="honest", owns=("src/owned.py",))
        data = self.registry()
        injected = dict(data["tasks"][0])
        injected["id"] = "injected"
        injected["ownedPaths"] = ["src/"]
        data["tasks"].append(injected)
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))
        code, text, _ = self.sbe("check")
        self.assertNotEqual(code, 0, "an injected collision must fail the scan")
        self.assertIn("COLLISION", text)
        self.assertIn("honest", text)
        self.assertIn("injected", text)


class TestNoData(TaskFixture):
    def test_a_base_commit_that_does_not_resolve_is_no_data_never_a_pass(self):
        self.open_task(base="0" * 40)
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertNotEqual(code, 0, "an unresolvable base must block, never pass")
        self.assertIn("NO-DATA", text)
        self.assertIn("not a pass", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "open", record)


class TestForce(TaskFixture):
    def test_force_records_the_disposition_and_marks_the_close_forced(self):
        self.open_task()
        write(self.repo, "docs/other.md", "out of scope\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1", "--force", "--who", "the operator",
                              "--why", "hotfix, accepted out of band")
        self.assertEqual(code, 0, text)
        self.assertIn("FORCED", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "closed", record)
        self.assertEqual(record["forced"]["who"], "the operator", record)
        self.assertEqual(record["forced"]["why"], "hotfix, accepted out of band", record)
        self.assertIn("docs/other.md", record["forced"]["violations"],
                      "the forced record must carry what it waived")

    def test_force_without_who_and_why_is_refused(self):
        self.open_task()
        write(self.repo, "docs/other.md", "out of scope\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1", "--force")
        self.assertNotEqual(code, 0, "a forced close with no author and no reason is an "
                                     "off switch, not a decision")


class TestReviewerSeparation(TaskFixture):
    def test_a_reviewer_cannot_open_owning_the_evidence_store(self):
        code, text, _ = self.open_task(task_id="rev", role="reviewer",
                                    owns=(".sbe/evidence/receipt.json",))
        self.assertNotEqual(code, 0, text)
        self.assertIn("evidence store", text)

    def test_a_reviewer_diff_touching_a_receipt_fails_even_with_force(self):
        self.open_task(task_id="rev", role="reviewer", owns=("docs/",))
        write(self.repo, ".sbe/evidence/receipt.json", "{\"forged\": true}\n")
        code, text, _ = self.sbe("close", "rev", "--force", "--who", "x", "--why", "y")
        self.assertNotEqual(code, 0, "force may not waive the reviewer-receipt class")
        self.assertIn("RECEIPT-VIOLATION", text)
        self.assertIn("may NOT waive", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "rev"][0]
        self.assertEqual(record["status"], "open", record)


class TestPeerOwned(TaskFixture):
    """PROTO-01: two honest writers on disjoint claims, one repository. A
    changed path outside the closing task's own ownedPaths that another OPEN
    task actually owns is not this task's violation; it is PEER-OWNED,
    named as such, and does not block the close. A path owned by nobody
    still fails exactly as before."""

    def test_a_path_owned_by_another_open_task_closes_clean_as_peer_owned(self):
        self.open_task(task_id="w1", owns=("src/owned.py",))
        self.open_task(task_id="w2", owns=("docs/other.md",))
        write(self.repo, "src/owned.py", "x = 2\n")
        write(self.repo, "docs/other.md", "peer's edit\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertEqual(code, 0, "a peer-owned changed path must not force w1: %s" % text)
        self.assertIn("PASS", text)
        self.assertIn("PEER-OWNED", text)
        self.assertIn("docs/other.md", text)
        self.assertIn("w2", text, "the PEER-OWNED line must name the owning task")
        self.assertNotIn("VIOLATION  docs/other.md", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "closed", record)
        self.assertNotIn("forced", record, "a peer-owned close must not need --force")

    def test_a_path_owned_by_nobody_still_fails_the_close(self):
        self.open_task(task_id="w1", owns=("src/owned.py",))
        self.open_task(task_id="w2", owns=("docs/other.md",))
        write(self.repo, "src/owned.py", "x = 2\n")
        write(self.repo, "unclaimed.txt", "nobody's path\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertNotEqual(code, 0, "an unowned changed path must still fail: %s" % text)
        self.assertIn("VIOLATION  unclaimed.txt", text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "w1"][0]
        self.assertEqual(record["status"], "open", record)


class TestFenceView(TaskFixture):
    def test_the_fence_view_renders_agent_per_line_from_the_registry(self):
        self.open_task(task_id="w1", agent="alpha", owns=("src/owned.py",))
        self.open_task(task_id="w2", agent="beta", owns=("docs/",))
        code, text, _ = self.sbe("fence")
        self.assertEqual(code, 0, text)
        lines = [l for l in text.splitlines() if l.startswith("- ")]
        self.assertEqual(len(lines), 2, text)
        for line in lines:
            self.assertIn("agent", line,
                          "the existing fence lint recognizes a live fence by the word "
                          "'agent'; a line without it is invisible to it")
            self.assertIn("files:", line, "a fence with no files: scope fences nothing")
        self.assertIn("src/owned.py", text)


class TestCorruptRegistry(TaskFixture):
    def test_a_corrupt_registry_is_refused_with_the_reason_and_never_reset(self):
        write(self.repo, os.path.join(".sbe", "tasks.json"), "{not json at all")
        code, text, _ = self.sbe("list")
        self.assertNotEqual(code, 0, "a corrupt registry must refuse, never read as empty")
        self.assertIn("does not parse", text)
        with io.open(os.path.join(self.repo, ".sbe", "tasks.json"),
                     encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not json at all",
                             "the refusal must not silently reset the file")

    def test_an_unknown_schema_version_is_refused(self):
        write(self.repo, os.path.join(".sbe", "tasks.json"),
              json.dumps({"schemaVersion": "9.9", "tasks": []}))
        code, text, _ = self.sbe("open", "--id", "w1", "--agent", "a", "--role", "writer",
                              "--base", self.base, "--verify", "true")
        self.assertNotEqual(code, 0, text)
        self.assertIn("schema version", text)


class TestDeletedRegistry(TaskFixture):
    """Pilot-team sandbox finding B5: `sbe task open` on a DELETED
    `.sbe/tasks.json` used to silently write a fresh, empty registry and
    lose the closed-task history; only `sbe status` and the Stop reconciler
    ever noticed the absence, never `open` itself.

    The signal this refuses on is the lock sidecar `.sbe/tasks.json.lock`:
    this module writes it only from inside an `open`/`close` read-modify-write
    (`_registry_lock`), never on its own, so a project that has never once
    run `sbe task open` has neither file, and the refusal cannot fire there
    (calibrated by the second test below). Nothing here deletes the sidecar
    or repairs the registry; the whole point is refusing without touching
    either file further."""

    def test_open_after_the_registry_file_is_deleted_is_refused_not_reset(self):
        code, text, _ = self.open_task(task_id="w1")
        self.assertEqual(code, 0, text)
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "w1")
        self.assertEqual(code, 0, text)  # a real CLOSED task now on record
        registry_file = os.path.join(self.repo, ".sbe", "tasks.json")
        lock_file = registry_file + ".lock"
        self.assertTrue(os.path.exists(lock_file), "fixture precondition: a lock "
                        "sidecar must exist once a registry has ever been opened")
        os.remove(registry_file)
        code, text, _ = self.open_task(task_id="w2", owns=("docs/other.md",))
        self.assertNotEqual(code, 0,
                            "opening on a deleted registry must refuse, never silently "
                            "start a fresh empty one over the lost history: %s" % text)
        self.assertIn(os.path.join(".sbe", "tasks.json"), text,
                      "the refusal must name the missing registry file")
        self.assertIn("lock", text.lower(),
                      "the refusal must name the signal it read")
        self.assertIn("history is gone", text, "the refusal must say what was lost")
        self.assertFalse(os.path.exists(registry_file),
                         "a refusal must never regenerate the registry it refused over")
        code, text, _ = self.sbe("list")
        self.assertIn("no open tasks", text, "w2 must not have been recorded either")

    def test_a_genuinely_first_ever_open_in_a_fresh_project_still_works(self):
        """Calibration for the refusal above: on a project that has NEVER
        opened a task, neither the registry nor its lock sidecar exists yet,
        so the same signal that catches a deletion must stay silent here and
        the open must succeed with no friction."""
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".sbe")),
                         "fixture precondition: setUp must not have touched .sbe/ yet")
        code, text, _ = self.open_task(task_id="w1")
        self.assertEqual(code, 0,
                         "a genuinely first-ever open in a fresh project must work with "
                         "no friction: %s" % text)


class TestTheOneOverlapRule(TaskFixture):
    def test_the_overlap_rule_is_imported_rather_than_reimplemented(self):
        """Two overlap rules would drift apart on the first change to either.
        This fails if the registry ever grows a local copy of the fence hook's
        paths_overlap."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            from brothersbe import tasks as mod
            import sbe_fence_hook
            self.assertIs(mod.paths_overlap, sbe_fence_hook.paths_overlap)
        finally:
            sys.path.pop(0)
            sys.path.pop(0)


class TestConcurrentWriters(TaskFixture):
    """T2's own defect, reproduced rather than assumed: `save_registry` used
    to be called straight off a plain, unlocked `load_registry`, so two
    concurrent `sbe task open` calls could both read the same tasks list,
    both append their own record to their OWN in-memory copy, and the second
    `save_registry` call would silently rewrite the file with a blob that
    never saw the first writer's task at all.

    These fixtures spawn REAL, separate processes, never threads inside one
    interpreter: the lock this task adds is an `fcntl.flock`, a cross-process
    primitive, and the defect only ever showed up across processes, two `sbe`
    invocations racing each other. Each worker is released from a filesystem
    barrier (an `O_CREAT|O_EXCL` marker per worker, then one shared "go" file
    the parent creates only once every marker exists) so the writes genuinely
    overlap rather than trickle in one interpreter start at a time, which is
    what this test's own precondition (a machine fast enough to schedule N
    process starts within a few milliseconds of each other) would otherwise
    require. The barrier removes that precondition: this test states it, and
    builds it, rather than depending on it.
    """

    def _worker_script(self):
        # A worker: prove it reached the barrier by planting its own ready
        # marker, wait for the shared go marker, then run the REAL `sbe
        # task open` CLI, exactly the way an agent would invoke it, against
        # its OWN distinct id and owned path. Every worker opens a DIFFERENT
        # task (no id collision, no owned-path overlap), so nothing here
        # depends on the overlap or reallocation logic; it isolates exactly
        # the lost-update race `save_registry` used to be exposed to.
        return (
            "import sys, os, time, subprocess\n"
            "sbe, root, ready_path, go_path, idx, base = sys.argv[1:7]\n"
            "fd = os.open(ready_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)\n"
            "os.close(fd)\n"
            "deadline = time.time() + 60\n"
            "while not os.path.exists(go_path):\n"
            "    if time.time() > deadline:\n"
            "        sys.stderr.write('TIMEOUT-WAITING-FOR-GO\\n')\n"
            "        sys.exit(1)\n"
            "    time.sleep(0.005)\n"
            "out = subprocess.run([sys.executable, sbe, 'task', 'open',\n"
            "    '--id', 'w%s' % idx, '--agent', 'agent%s' % idx, '--role', 'writer',\n"
            "    '--base', base, '--verify', 'true',\n"
            "    '--owns', 'src/owned-%s.py' % idx, '--cwd', root],\n"
            "    capture_output=True, text=True)\n"
            "sys.stdout.write(out.stdout)\n"
            "sys.stderr.write(out.stderr)\n"
            "sys.exit(out.returncode)\n"
        )

    def _run_concurrent_writers(self, n, root, base):
        """Launch `n` real `sbe task open` subprocesses against `root`'s task
        registry, all released at once from a shared filesystem barrier.
        Returns a list of `(index, stdout, stderr, returncode)`, one per
        worker."""
        barrier = tempfile.mkdtemp(prefix="sbe-tasks-barrier-")
        go_path = os.path.join(barrier, "go")
        script = self._worker_script()
        procs = []
        for i in range(n):
            ready_path = os.path.join(barrier, "ready-%03d" % i)
            proc = subprocess.Popen(
                [sys.executable, "-c", script, SBE, root, ready_path, go_path, str(i), base],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            procs.append((i, ready_path, proc))
        deadline = time.time() + 60
        while not all(os.path.exists(ready) for _, ready, _ in procs):
            if time.time() > deadline:
                self.fail("not every one of %d worker(s) reached the barrier within 60s" % n)
            time.sleep(0.01)
        fd = os.open(go_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        results = []
        for i, _, proc in procs:
            out, err = proc.communicate(timeout=120)
            results.append((i, out, err, proc.returncode))
        return results

    def test_twenty_concurrent_writers_open_distinct_tasks_all_survive(self):
        """THE DONE-CHECK: 20 real processes, one temp registry, 20 distinct
        task ids and owned paths. Every one of the 20 must survive as its own
        open task in the final registry: 0 lost, 20 records on disk."""
        n = 20
        results = self._run_concurrent_writers(n, self.repo, self.base)
        for i, out, err, code in results:
            self.assertEqual(code, 0, "worker %d exited %s: stdout=%r stderr=%r"
                             % (i, code, out, err))
        data = self.registry()
        ids = sorted(t["id"] for t in data["tasks"])
        expected = sorted("w%d" % i for i in range(n))
        self.assertEqual(ids, expected,
                         "expected %d distinct open tasks, got %d: %r"
                         % (n, len(ids), ids))
        for i in range(n):
            matches = [t for t in data["tasks"] if t["id"] == "w%d" % i]
            self.assertEqual(len(matches), 1, "task w%d must appear exactly once" % i)
            record = matches[0]
            self.assertEqual(record["status"], "open", record)
            self.assertEqual(record["ownedPaths"], ["src/owned-%d.py" % i], record)


class TestChangeScopedIdentity(TaskFixture):
    """Card 1: a task's identity is the pair (change, taskId), not taskId
    alone. Today (before this fix) `sbe work start` derives `change` from a
    dossier's basename and every derived plan starts fresh at "T01"
    (docs/KNOWN-LIMITS.md), so T01 in one dossier collided with T01 in
    another. These fixtures exercise the registry mechanism directly, at the
    layer that carries the fix, rather than through a second, path-disjoint
    derived plan: `--change` is the same string `sbe work start` computes and
    passes through, so this is the real mechanism, not a shortcut around it.
    """

    def test_the_same_task_id_in_two_different_changes_does_not_collide(self):
        code, text, _ = self.open_task(task_id="T01", change="design/dossier-a",
                                       owns=("src/owned.py",))
        self.assertEqual(code, 0, text)
        code, text, _ = self.open_task(task_id="T01", change="design/dossier-b",
                                       owns=("docs/other.md",))
        self.assertEqual(code, 0,
                         "T01 open in a DIFFERENT change must not block opening T01 in "
                         "this one: identity is (change, taskId), not id alone, and this "
                         "used to collide: %s" % text)
        records = [t for t in self.registry()["tasks"] if t["id"] == "T01"]
        self.assertEqual(len(records), 2, records)
        self.assertEqual(sorted(r["change"] for r in records),
                         ["design/dossier-a", "design/dossier-b"], records)
        for r in records:
            self.assertEqual(r["status"], "open", r)

    def test_the_same_task_id_in_the_same_change_still_refuses(self):
        """Calibration precondition for the test above, stated as its own
        assertion: if reopening T01 in the SAME change ever stopped
        refusing, the "does not collide" test above would prove nothing
        (two open writers over the same declaration is not an identity fix,
        it is the collision this registry exists to prevent)."""
        code, text, _ = self.open_task(task_id="T01", change="design/dossier-a",
                                       owns=("src/owned.py",))
        self.assertEqual(code, 0, text)
        code, text, _ = self.open_task(task_id="T01", change="design/dossier-a",
                                       owns=("docs/other.md",))
        self.assertNotEqual(code, 0,
                            "reopening T01 in the SAME change while it is open must still "
                            "refuse, exactly as before this pair existed: %s" % text)
        self.assertIn("T01", text)
        self.assertIn("design/dossier-a", text)

    def test_sbe_task_list_json_names_the_change_a_task_belongs_to(self):
        # `sbe task list`'s plain-text line keeps its ORIGINAL column layout
        # on purpose: docs/book/07-the-task-registry.md (outside this
        # change's fence) pins it byte-for-byte against live output.
        # `--json` dumps the raw records, so the pair is checked there.
        self.open_task(task_id="T01", change="design/dossier-a")
        code, text, _ = self.sbe("list", "--json")
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        record = [t for t in payload["openTasks"] if t["id"] == "T01"][0]
        self.assertEqual(record["change"], "design/dossier-a",
                         "sbe task list --json must name the change a task belongs to")

    def test_close_with_a_bare_id_open_in_two_changes_is_refused_as_ambiguous(self):
        self.open_task(task_id="T01", change="design/dossier-a", owns=("src/owned.py",))
        self.open_task(task_id="T01", change="design/dossier-b", owns=("docs/other.md",))
        code, text, _ = self.sbe("close", "T01")
        self.assertNotEqual(code, 0,
                            "a bare id open in two different changes cannot be closed "
                            "without saying which one: %s" % text)
        self.assertIn("design/dossier-a", text)
        self.assertIn("design/dossier-b", text)
        self.assertIn("--change", text)
        records = [t for t in self.registry()["tasks"] if t["id"] == "T01"]
        self.assertEqual(len(records), 2, records)
        for r in records:
            self.assertEqual(r["status"], "open",
                             "a refused, ambiguous close must not touch either record: %r"
                             % r)

    def test_close_with_change_disambiguates_and_closes_only_that_record(self):
        self.open_task(task_id="T01", change="design/dossier-a", owns=("src/owned.py",))
        self.open_task(task_id="T01", change="design/dossier-b", owns=("docs/other.md",))
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "T01", "--change", "design/dossier-a")
        self.assertEqual(code, 0, text)
        by_change = {r["change"]: r for r in self.registry()["tasks"] if r["id"] == "T01"}
        self.assertEqual(by_change["design/dossier-a"]["status"], "closed", by_change)
        self.assertEqual(by_change["design/dossier-b"]["status"], "open", by_change)

    def test_close_strips_change_the_same_way_open_stores_it(self):
        """Round 2 fix: `cmd_open` stores `--change` STRIPPED
        (`(args.change or "").strip()`), but `cmd_close` used to read it RAW
        (`getattr(args, "change", None)`, no `.strip()`). A padded
        `--change` therefore opened fine (stored as the stripped value) and
        then could never be closed by giving the SAME padded value back: the
        raw lookup compared the padded string against the stripped one and
        never matched, refusing with a message that misleadingly reads as
        'no such open task' for a task that IS open."""
        code, text, _ = self.open_task(task_id="W9", change="  pad  ",
                                       owns=("src/owned.py",))
        self.assertEqual(code, 0, text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "W9"][0]
        self.assertEqual(record["change"], "pad",
                         "fixture precondition: open already stores change stripped")
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "W9", "--change", "  pad  ")
        self.assertEqual(code, 0,
                         "closing with the SAME padded --change value open used must "
                         "succeed: a task open under a stripped change must be "
                         "addressable by the identical padded string, not silently "
                         "unreachable: %s" % text)
        record = [t for t in self.registry()["tasks"] if t["id"] == "W9"][0]
        self.assertEqual(record["status"], "closed", record)


class TestSchemaMigration(TaskFixture):
    """schemaVersion 1.0 (identity: id alone) to 1.1 (identity: the
    (change, taskId) pair). The migration is explicit and versioned, runs
    ONLY on a write path, and a registry it cannot migrate is refused by
    name, never silently rewritten."""

    def _write_1_0_registry(self, task_id="legacy", status="open"):
        record = {
            "id": task_id, "agent": "old-agent", "role": "writer", "worktree": None,
            "ownedPaths": ["src/owned.py"], "readOnlyPaths": [], "baseCommit": self.base,
            "expiry": None, "status": status, "verifyCommand": "true",
            "evidenceId": None, "openedAt": "2026-01-01T00:00:00Z",
            "closedAt": None if status == "open" else "2026-01-02T00:00:00Z",
        }
        data = {"schemaVersion": "1.0", "tasks": [record]}
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))
        return record

    def test_a_1_0_registry_migrates_to_1_1_on_first_write_round_trip(self):
        original = self._write_1_0_registry(task_id="legacy", status="open")
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "legacy")
        self.assertEqual(code, 0, text)

        data = self.registry()
        self.assertEqual(data["schemaVersion"], "1.1",
                         "the FIRST write after loading a 1.0 registry must migrate it "
                         "to the current schema on disk")
        record = [t for t in data["tasks"] if t["id"] == "legacy"][0]
        self.assertEqual(record["change"], "",
                         "a pre-migration record has no recoverable dossier, so it "
                         "adopts the empty, unscoped change rather than a guessed one")
        for key in ("id", "agent", "role", "worktree", "ownedPaths", "readOnlyPaths",
                    "baseCommit", "expiry", "verifyCommand", "evidenceId", "openedAt"):
            self.assertEqual(record[key], original[key],
                             "field %r must round-trip unchanged through migration" % key)
        self.assertEqual(record["status"], "closed", record)

    def test_a_1_0_registry_migration_preserves_top_level_controller_leases(self):
        self._write_1_0_registry(task_id="legacy", status="closed")
        data = self.registry()
        lease = {
            "leaseId": "a" * 32, "change": "widget-cache", "taskId": "T01",
            "worktree": os.path.realpath(os.path.join(self.repo, "..", "candidate")),
            "controller": "claude:fable:session", "acquiredAt": "2026-01-01T00:00:00Z",
            "renewedAt": "2026-01-01T00:00:00Z", "expiresAt": "2099-01-01T00:00:00Z",
            "releasedAt": None, "release": None,
        }
        data["controllerLeases"] = [lease]
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))
        code, text, _ = self.open_task(task_id="new", change="new-change",
                                       owns=("docs/other.md",))
        self.assertEqual(code, 0, text)
        migrated = self.registry()
        self.assertEqual(migrated["schemaVersion"], "1.1")
        self.assertIn("controllerLeases", migrated)
        self.assertEqual(migrated["controllerLeases"], [lease])
    def test_list_reads_a_1_0_registry_as_is_and_never_migrates_it_on_disk(self):
        self._write_1_0_registry(task_id="legacy", status="open")
        path = os.path.join(self.repo, ".sbe", "tasks.json")
        with io.open(path, encoding="utf-8") as fh:
            before = fh.read()
        code, text, _ = self.sbe("list")
        self.assertEqual(code, 0, text)
        self.assertIn("legacy", text)
        with io.open(path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(after, before,
                         "a read-only command must never persist a migrated copy: the "
                         "on-disk file changes shape on the FIRST WRITE, never a read")

    def test_a_registry_that_cannot_be_migrated_is_refused_by_name_never_rewritten(self):
        self._write_1_0_registry(task_id="legacy", status="open")
        data = self.registry()
        # A hand-edit no migration can interpret: `change` present on a 1.0
        # record, but not a string. Calibrated against migrate_registry's own
        # type check: removing that check (proven separately, see this
        # session's report) turns this fixture green on a coerced, silently
        # wrong migration instead of a refusal.
        data["tasks"][0]["change"] = 12345
        raw = json.dumps(data)
        write(self.repo, os.path.join(".sbe", "tasks.json"), raw)

        code, text, _ = self.sbe("close", "legacy")
        self.assertNotEqual(code, 0, text)
        self.assertIn("legacy", text)
        self.assertIn("non-string", text.lower())
        with io.open(os.path.join(self.repo, ".sbe", "tasks.json"), encoding="utf-8") as fh:
            on_disk = fh.read()
        self.assertEqual(on_disk, raw,
                         "a registry that cannot be migrated must be refused, never "
                         "silently rewritten")

    def test_a_migrated_open_legacy_record_no_longer_blocks_a_change_scoped_reopen(self):
        """Disclosure (round 2), not a bug fix: docs/KNOWN-LIMITS.md and this
        class's own docstring already say a migrated record adopts the
        empty, unscoped change ''. What round 1 left UNSTATED is the
        consequence for the OPEN-collision check specifically: identity is
        now the (change, id) pair, so a migrated OPEN record's change=''
        no longer matches a DIFFERENT, non-empty change stamped onto a
        fresh `sbe task open`/`sbe work start` for the SAME id. Before this
        pair existed, one global OPEN-by-id-alone check refused that
        reopen outright. After it, the reopen SUCCEEDS: the registry ends
        up with TWO OPEN records for one id (the legacy one, and the new
        change-scoped one), and a bare `sbe task close <id>` on that id
        becomes ambiguous. `--change ''` is the working escape hatch that
        still addresses the legacy record specifically, proven below."""
        self._write_1_0_registry(task_id="legacy", status="open")
        code, text, _ = self.open_task(task_id="legacy", change="design/dossier-alpha",
                                       owns=("docs/other.md",))
        self.assertEqual(code, 0,
                         "a migrated legacy OPEN record (change='') must not block a "
                         "change-scoped open of the SAME id: identity is the (change, "
                         "id) pair now, and '' != 'design/dossier-alpha': %s" % text)
        records = [t for t in self.registry()["tasks"] if t["id"] == "legacy"]
        self.assertEqual(len(records), 2, records)
        by_change = {r["change"]: r for r in records}
        self.assertEqual(by_change[""]["status"], "open", records)
        self.assertEqual(by_change["design/dossier-alpha"]["status"], "open", records)

        # The escape hatch: `--change ''` still addresses the legacy record
        # on its own, and closes only it.
        write(self.repo, "src/owned.py", "x = 2\n")
        self.commit()
        code, text, _ = self.sbe("close", "legacy", "--change", "")
        self.assertEqual(code, 0,
                         "the empty change must still be a valid, working --change "
                         "value that addresses the legacy record specifically: %s" % text)
        by_change = {r["change"]: r for r in self.registry()["tasks"]
                    if r["id"] == "legacy"}
        self.assertEqual(by_change[""]["status"], "closed", by_change)
        self.assertEqual(by_change["design/dossier-alpha"]["status"], "open", by_change)


class TestControllerLeaseRegistryValidation(TaskFixture):
    def test_absent_controller_leases_remains_valid_for_legacy_1_1_registry(self):
        code, text, _ = self.open_task()
        self.assertEqual(code, 0, text)
        self.assertNotIn("controllerLeases", self.registry())

    def test_malformed_controller_leases_fail_closed(self):
        bad_values = ({}, [None], [{"leaseId": "short"}])
        for bad in bad_values:
            with self.subTest(value=bad):
                write(self.repo, os.path.join(".sbe", "tasks.json"),
                      json.dumps({"schemaVersion": "1.1", "tasks": [],
                                  "controllerLeases": bad}))
                code, text, _ = self.sbe("list")
                self.assertNotEqual(code, 0, text)
                self.assertIn("controllerLeases", text)


class TestSessionIdentity(TaskFixture):
    """`tools/sbe_fence_hook.py` cannot distinguish a task's rightful holder
    from any other session unless `sbe task open` records one. This is the
    other half of that fix: the CLI side. `CLAUDE_SESSION_ID` and
    `SBE_SESSION_ID` (checked in that order) are read from the process
    environment, never invented, so a call made with neither set records no
    session at all rather than a placeholder the hook could mistake for a
    real claim."""

    def _open_with_env(self, extra_env, task_id="w1"):
        env = dict(os.environ)
        for k in ("CLAUDE_SESSION_ID", "SBE_SESSION_ID"):
            env.pop(k, None)
        env.update(extra_env)
        out = subprocess.run(
            [sys.executable, SBE, "task", "open", "--id", task_id, "--agent", "alpha",
             "--role", "writer", "--base", self.base, "--verify", "true",
             "--owns", "src/owned.py", "--cwd", self.repo],
            capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return [t for t in self.registry()["tasks"] if t["id"] == task_id][0]

    def test_open_records_the_session_when_claude_session_id_is_set(self):
        record = self._open_with_env({"CLAUDE_SESSION_ID": "session-aaaa-1111"})
        self.assertEqual(record.get("session"), "session-aaaa-1111")

    def test_open_records_the_session_from_sbe_session_id_when_claude_session_id_is_absent(self):
        record = self._open_with_env({"SBE_SESSION_ID": "session-bbbb-2222"})
        self.assertEqual(record.get("session"), "session-bbbb-2222")

    def test_claude_session_id_wins_over_sbe_session_id(self):
        record = self._open_with_env({"CLAUDE_SESSION_ID": "claude-wins",
                                      "SBE_SESSION_ID": "sbe-loses"})
        self.assertEqual(record.get("session"), "claude-wins")

    def test_open_omits_the_session_key_entirely_when_neither_variable_is_set(self):
        record = self._open_with_env({})
        self.assertNotIn("session", record,
                         "an absent identity must be an absent key, never a "
                         "guessed or empty placeholder the fence hook could "
                         "mistake for a real claim")


class TestHelpMeansHelp(unittest.TestCase):
    """`sbe task open -h` printed the right usage and exited 2: the module
    caught argparse's SystemExit(0) for help and folded it into exit_usage,
    and a LEADING `-h` (`sbe task -h`) never reached this module at all,
    refused by the top-level parser. Help is not an error. Calibrated by
    reinjecting the bare `except SystemExit: return exit_usage` and the
    argparse-first dispatch in cli.py: each fixture failed on a returncode of
    2 where 0 was asserted, before the fix was restored, the restore verified
    against the pre-recorded `git hash-object` of the fixed files."""

    def run_sbe(self, *argv):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        out = subprocess.run([sys.executable, SBE, "task"] + list(argv),
                             capture_output=True, text=True, cwd=tmp)
        return out.returncode, out.stdout + out.stderr, tmp

    def test_help_exits_0_with_usage_and_opens_nothing(self):
        for argv in (("-h",), ("--help",), ("open", "-h"), ("close", "-h"),
                     ("list", "-h"), ("fence", "-h"), ("check", "-h")):
            code, text, tmp = self.run_sbe(*argv)
            self.assertEqual(code, 0, "sbe task %s exited %d: %s"
                             % (" ".join(argv), code, text))
            self.assertIn("usage", text.lower(),
                          "sbe task %s printed no usage: %r" % (" ".join(argv), text))
            self.assertFalse(os.path.exists(os.path.join(tmp, ".sbe")),
                             "sbe task %s created a registry it was only asked to "
                             "describe" % " ".join(argv))

    def test_a_genuinely_bad_flag_still_exits_2(self):
        for argv in (("--bogus",), ("open", "--bogus")):
            code, text, _tmp = self.run_sbe(*argv)
            self.assertEqual(code, 2, "sbe task %s exited %d, not the usage error 2: %s"
                             % (" ".join(argv), code, text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
