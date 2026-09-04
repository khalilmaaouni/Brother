#!/usr/bin/env python3
"""BrotherSBE regression tests. Standard library only (no pip install), matching
the zero-dependency ethos of the tools. Run: python3 tools/test_sbe.py

These exist because an external review found a real secret-leak in the resume
brief that a test would have caught. Each test here guards a claim the project
makes about itself: secrets are redacted, sensitive files are owner-only, project
identity does not collide, and the autosave captures untracked work non-invasively.
"""
import ast, contextlib, glob, hashlib, io, os, json, re, shutil, stat, sys, tempfile, time, subprocess, importlib.util
from unittest import mock

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
ROOT = os.path.dirname(HERE)


def _posix_modes_enforced():
    """True when this filesystem actually restricts a newly created file to
    the exact mode bits requested, asked of the filesystem itself rather
    than of sys.platform -- the same idiom TestCaseVariantPaths._folds_case
    in test_sbe_bash_guard.py uses for case-folding, applied to permission
    bits. False on a platform that ignores the requested mode (Windows:
    os.open(path, ..., 0o600) silently yields 0o666 there). The two
    owner-only tests below branch on this rather than on sys.platform, so a
    POSIX host that happens to run on a mode-ignoring filesystem (a FAT/exFAT
    mount, for one) gets the honest branch too."""
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "probe")
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        return stat.S_IMODE(os.stat(probe).st_mode) == 0o600

# The digest injected unconditionally at every session start used to spend
# roughly 9KB, almost all of the 10KB hook cap, on 24 law lines: a release
# review measured that as ~2200-2300 tokens pushed into context before the
# user asked for anything. The fix cut DIGEST.md down to the three
# unconditional laws (L6, L11, L14), the checked/human legend, the NO-DATA
# statement, the after-compaction-or-resume pointer, and a pointer to
# references/laws-full-digest.md for everything else, verbatim and unabridged.
# This ceiling is a conservative byte proxy for the release target of well
# under 800 tokens (roughly 3200 bytes at ~4 bytes/token), set with headroom
# over the measured post-fix size (~2.4KB) so DIGEST.md can grow a little
# without silently regressing back toward the old budget. It is deliberately
# smaller than the 10k-char hook cap TestDigestCap enforces above: that cap
# bounds what the harness will accept, this one bounds what the digest should
# actually cost.
DIGEST_BYTE_CEILING = 3000

# Import sbe_telemetry as a module regardless of cwd.
spec = importlib.util.spec_from_file_location("sbe_telemetry", os.path.join(HERE, "sbe_telemetry.py"))
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)
sys.modules["sbe_telemetry"] = bm

import unittest


class TestRedaction(unittest.TestCase):
    def test_secret_shapes_are_masked(self):
        cases = [
            "the prod password is hunter2",
            "PROD_DB_PASSWORD=s3cr3tvalue",
            "sk-ant-api03-ABCDEFGHIJKLMNOP",
            "Authorization: Bearer abcdef1234567890xyz",
            "ssn 123-45-6789",
        ]
        for c in cases:
            clean, n = bm.redact(c)
            self.assertGreater(n, 0, "no redaction fired on: %s" % c)
            self.assertIn("[REDACTED]", clean)
        # a benign correction must survive intact
        clean, n = bm.redact("always use the staging bucket, never production")
        self.assertEqual(n, 0)
        self.assertIn("staging bucket", clean)


class TestResumeBrief(unittest.TestCase):
    def _write_transcript(self, path):
        msgs = [{"type": "user", "message": {"content": "the prod password is hunter2"}}]
        msgs += [{"type": "assistant", "message": {"content": [
            {"type": "text", "text": "using token sk-ant-api03-ABCDEFGHIJKLMNOP"},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "curl -H 'Authorization: Bearer abcdef1234567890xyz' x"}}]}}]
        io.open(path, "w").write("\n".join(json.dumps(m) for m in msgs))
        return path

    def test_default_writes_no_brief_and_names_the_switch_on_stderr(self):
        """Old meaning (before this test was rewritten): the resume brief used
        to be written UNCONDITIONALLY, real content when transcript capture was
        on, a "[REDACTED]" placeholder in its place when off, so this test
        proved that even the placeholder-carrying default file never let a raw
        secret from the transcript reach disk, and was owner-only. Flip
        decision (founder, 2026-07-29): the resume brief was the one capture
        path still writing a file by default, unlike `metrics` and
        `corrections`, which write nothing until switched on. Flipped to match:
        `BROTHERSBE_TELEMETRY_TRANSCRIPT` off (the default) now means no file
        at all, and the code path that would have written it names the switch
        once on stderr, so an absent file is never mistaken for a quiet
        session. This test now asserts THAT default: no brief on disk, and the
        switch named on stderr. The redaction and owner-only guards the old
        test carried now live in test_opt_in_writes_the_brief_and_still_redacts,
        which is the only path that still writes real content."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["BROTHERSBE_VAULT"] = os.path.join(d, "vault")
            # rebuild the module's paths against the temp vault
            import importlib
            importlib.reload(bm)
            repo = os.path.join(d, "acme", "backend")
            os.makedirs(repo)
            tp = self._write_transcript(os.path.join(d, "t.jsonl"))
            payload = json.dumps({"transcript_path": tp, "cwd": repo})
            old_stdin, old_stderr = sys.stdin, sys.stderr
            sys.stdin, sys.stderr = io.StringIO(payload), io.StringIO()
            try:
                bm.cmd_precompact_brief()
                err = sys.stderr.getvalue()
            finally:
                sys.stdin, sys.stderr = old_stdin, old_stderr
            teldir = os.path.join(os.environ["BROTHERSBE_VAULT"], "99-System", "telemetry")
            briefs = ([f for f in os.listdir(teldir) if f.startswith("last-resume-")]
                      if os.path.isdir(teldir) else [])
            self.assertEqual(briefs, [], "the default resume brief wrote a file: %r" % briefs)
            self.assertIn("BROTHERSBE_TELEMETRY_TRANSCRIPT", err,
                          "the withheld path did not name its switch on stderr: %r" % err)

    def test_opt_in_writes_the_brief_and_still_redacts(self):
        """New fixture (founder, 2026-07-29, the transcript-brief opt-in flip):
        with BROTHERSBE_TELEMETRY_TRANSCRIPT=1 the brief is written for real,
        from real transcript content, and the existing redaction and
        owner-only guards still hold: secrets never reach the file."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["BROTHERSBE_VAULT"] = os.path.join(d, "vault")
            os.environ["BROTHERSBE_TELEMETRY_TRANSCRIPT"] = "1"
            import importlib
            importlib.reload(bm)
            repo = os.path.join(d, "acme", "backend")
            os.makedirs(repo)
            tp = self._write_transcript(os.path.join(d, "t.jsonl"))
            payload = json.dumps({"transcript_path": tp, "cwd": repo})
            old = sys.stdin
            sys.stdin = io.StringIO(payload)
            # Spy on the mode every os.open call actually requests, so the
            # Windows branch below can assert the reachable guarantee (the
            # writer asked for owner-only) even where the platform will not
            # honor it.
            requested = []
            real_open = os.open
            def spy_open(path, flags, mode=0o777, *a, **kw):
                requested.append((path, mode))
                return real_open(path, flags, mode, *a, **kw)
            try:
                with mock.patch("os.open", side_effect=spy_open):
                    bm.cmd_precompact_brief()
            finally:
                sys.stdin = old
                os.environ.pop("BROTHERSBE_TELEMETRY_TRANSCRIPT", None)
            teldir = os.path.join(os.environ["BROTHERSBE_VAULT"], "99-System", "telemetry")
            briefs = [f for f in os.listdir(teldir) if f.startswith("last-resume-")]
            self.assertEqual(len(briefs), 1, "opt-in must write exactly one resume brief")
            path = os.path.join(teldir, briefs[0])
            body = io.open(path).read()
            for secret in ("hunter2", "sk-ant-api03-ABCDEFGHIJKLMNOP", "abcdef1234567890xyz"):
                self.assertNotIn(secret, body, "resume brief leaked: %s" % secret)
            self.assertIn("[REDACTED]", body)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if _posix_modes_enforced():
                self.assertEqual(mode, 0o600, "resume brief must be owner-only, got %o" % mode)
            else:
                # The platform ignores the requested mode (Windows: mode
                # lands at 0o666 no matter what was asked for). The
                # reachable guarantee is that the writer itself requested
                # owner-only -- _write_brief's os.open call -- not that the
                # platform delivered it.
                asked = [m for p, m in requested if p == path]
                self.assertTrue(asked, "resume brief was not written through os.open: %r"
                                % requested)
                self.assertEqual(asked[-1], 0o600,
                                 "resume brief writer did not request owner-only, got %o"
                                 % asked[-1])


class TestProjectIdentity(unittest.TestCase):
    def test_same_basename_different_path_no_collision(self):
        a = bm._project_of("/tmp/client-a/backend")
        b = bm._project_of("/tmp/client-b/backend")
        self.assertNotEqual(a, b, "same-basename projects collided: %s == %s" % (a, b))
        self.assertEqual(a, bm._project_of("/tmp/client-a/backend"), "identity must be stable")


class TestAutosave(unittest.TestCase):
    def test_snapshot_captures_untracked_without_touching_tree(self):
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            def git(*a):
                return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
            io.open(os.path.join(repo, "tracked.txt"), "w").write("v1")
            git("add", "-A"); git("commit", "-qm", "init")
            io.open(os.path.join(repo, "tracked.txt"), "w").write("v2")
            io.open(os.path.join(repo, "untracked_new.txt"), "w").write("WIP-WORK")
            before = git("status", "--porcelain").stdout
            before_head = git("rev-parse", "HEAD").stdout
            vdir = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vdir)
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the snapshot ref it creates is asserted below
                           text=True, env=env)
            # working tree and branch untouched
            self.assertEqual(before, git("status", "--porcelain").stdout)
            self.assertEqual(before_head, git("rev-parse", "HEAD").stdout)
            # ref created and it contains the untracked file
            # The ref is namespaced per worktree; resolve it rather than
            # hardcoding an id the test would have to re-derive.
            refs = git("for-each-ref", "--format=%(refname)",
                       "refs/brothersbe/autosave").stdout.split()
            self.assertEqual(len(refs), 1, "expected one autosave ref, got %r" % refs)
            self.assertTrue(refs[0].startswith("refs/brothersbe/autosave/"),
                            "autosave ref is not namespaced per worktree: %r" % refs[0])
            shown = git("show", "%s:untracked_new.txt" % refs[0]).stdout
            self.assertIn("WIP-WORK", shown, "autosave did not capture the untracked file")
            # secret-shaped files must NOT enter the snapshot
            io.open(os.path.join(repo, ".env"), "w").write("SECRET=leak")
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the snapshot ref it creates is asserted below
                           text=True, env=env)
            envobj = git("cat-file", "-e", "%s:.env" % refs[0])
            self.assertNotEqual(envobj.returncode, 0, ".env leaked into the autosave snapshot")


class TestAutosaveExclusions(unittest.TestCase):
    def test_excluded_tracked_files_ride_at_head_and_modern_keys_stay_out(self):
        """Two halves of one review finding. The exclusion list stopped at
        id_rsa/id_dsa, so a fresh id_ed25519 (ssh-keygen's default since
        OpenSSH 8.5) and an .envrc entered the snapshot as permanent git
        objects. And the snapshot was built in a fresh temp index, so a
        TRACKED file matching an exclusion vanished from the snapshot
        entirely, with its unsaved edit, while the comment said tracked files
        were unaffected. Now: the index is seeded from HEAD (excluded tracked
        files ride at their last-committed state), and the modern secret
        shapes stay out."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            def git(*a):
                return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
            io.open(os.path.join(repo, ".env"), "w").write("SECRET=v1")
            io.open(os.path.join(repo, "app.py"), "w").write("print('hi')\n")
            git("add", "-A", "-f"); git("commit", "-qm", "init")
            io.open(os.path.join(repo, ".env"), "w").write("SECRET=v2-unsaved-edit")
            io.open(os.path.join(repo, "id_ed25519"), "w").write("PRIVATE KEY MATERIAL")
            io.open(os.path.join(repo, ".envrc"), "w").write("AWS_SECRET=hunter2")
            io.open(os.path.join(repo, "wip.txt"), "w").write("UNLANDED")
            vdir = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vdir)
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the ref content is asserted below
                           text=True, env=env)
            ref = git("for-each-ref", "--format=%(refname)",
                      "refs/brothersbe/autosave").stdout.split()[0]
            # tracked excluded file: present at its HEAD state, edit not captured
            shown = git("show", "%s:.env" % ref)
            self.assertEqual(shown.stdout, "SECRET=v1",
                             "tracked excluded file dropped or edit captured: %r" % shown.stdout)
            # modern secret shapes stay out
            for name in ("id_ed25519", ".envrc"):
                r = git("cat-file", "-e", "%s:%s" % (ref, name))
                self.assertNotEqual(r.returncode, 0, "%s leaked into the snapshot" % name)
            # the actual work is captured
            self.assertIn("UNLANDED", git("show", "%s:wip.txt" % ref).stdout)


class TestAutosaveCoversTheWorktree(unittest.TestCase):
    def _repo(self, root):
        def git(*a):
            return subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)
        git("init", "-q")
        git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
        os.makedirs(os.path.join(root, "frontend"))
        os.makedirs(os.path.join(root, "backend"))
        io.open(os.path.join(root, "frontend", "index.js"), "w").write("v1")
        io.open(os.path.join(root, "backend", "app.py"), "w").write("v1")
        git("add", "-A"); git("commit", "-qm", "init")
        return git

    def test_subdirectory_session_snapshots_the_whole_tree(self):
        """The hook's cwd is wherever the session sat (a package directory in
        a monorepo), and the snapshot used to cover only that subdirectory
        while the ref named the whole worktree: out-of-cwd edits rode at
        HEAD (work that looked never done) and a brand-new out-of-cwd file
        was absent outright. The snapshot now runs from the worktree top."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            git = self._repo(repo)
            io.open(os.path.join(repo, "frontend", "index.js"), "w").write("v2-EDIT")
            io.open(os.path.join(repo, "frontend", "newfeature.js"), "w").write("NEW-WORK")
            env = dict(os.environ, BROTHERSBE_VAULT=tempfile.mkdtemp())
            subprocess.run([sys.executable, hook, "precompact"],  # sbe: allow-silent test harness fires the hook; the ref content is asserted below
                           input=json.dumps({"cwd": os.path.join(repo, "backend")}),
                           text=True, env=env, timeout=120)
            ref = git("for-each-ref", "--format=%(refname)",
                      "refs/brothersbe/autosave").stdout.split()[0]
            self.assertEqual("v2-EDIT", git("show", "%s:frontend/index.js" % ref).stdout,
                             "an out-of-cwd edit rode at HEAD instead of being saved")
            self.assertEqual("NEW-WORK", git("show", "%s:frontend/newfeature.js" % ref).stdout,
                             "an out-of-cwd untracked file was absent from the snapshot")

    def test_out_of_cwd_only_work_still_writes_a_ref(self):
        """When every unlanded change sat outside the session's cwd, the old
        subdirectory-scoped add staged nothing, the identical-tree branch
        returned early, and the hook exited 0 with no ref and no log line:
        work on disk, and recover later said no autosave exists."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            git = self._repo(repo)
            io.open(os.path.join(repo, "frontend", "index.js"), "w").write("HOURS-OF-WORK")
            vault = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            subprocess.run([sys.executable, hook, "precompact"],  # sbe: allow-silent test harness fires the hook; the ref content is asserted below
                           input=json.dumps({"cwd": os.path.join(repo, "backend")}),
                           text=True, env=env, timeout=120)
            refs = git("for-each-ref", "--format=%(refname)",
                       "refs/brothersbe/autosave").stdout.split()
            self.assertEqual(1, len(refs), "no snapshot ref for out-of-cwd work")
            self.assertIn("HOURS-OF-WORK", git("show", "%s:frontend/index.js" % refs[0]).stdout)

    def test_parallel_ticks_lose_no_update_and_a_superseded_snapshot_stays_reachable(self):
        """Two serialization halves. The tick counter was an unlocked
        read-modify-write fired from PostToolUse, so parallel tool calls lost
        updates: the throttle skipped snapshot points and the runaway warning
        printed a count below the real one. And the single-slot ref had no
        reflog, so a newer snapshot made the older one unreachable."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            git = self._repo(repo)
            vault = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vault, BROTHERSBE_AUTOSAVE="1")
            procs = [subprocess.Popen([sys.executable, hook, "tick", "sess1"], stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL, text=True, env=env)
                     for _ in range(30)]
            for p in procs:
                p.communicate(json.dumps({"cwd": repo}), timeout=120)
            tel = os.path.join(vault, "99-System", "telemetry")
            ctr = [f for f in os.listdir(tel) if f.startswith(".autosave-tick")
                   and not f.endswith((".lock", ".warned"))][0]
            self.assertEqual("30", io.open(os.path.join(tel, ctr)).read(),
                             "parallel ticks lost counter updates")
            # reflog half: snapshot good work, destroy it, snapshot again
            io.open(os.path.join(repo, "good.txt"), "w").write("GOOD")
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the reflog is asserted below
                           text=True, env=env, timeout=120)
            ref = git("for-each-ref", "--format=%(refname)",
                      "refs/brothersbe/autosave").stdout.split()[0]
            os.remove(os.path.join(repo, "good.txt"))
            io.open(os.path.join(repo, "other.txt"), "w").write("LATER")
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the reflog is asserted below
                           text=True, env=env, timeout=120)
            self.assertEqual("GOOD", git("show", "%s@{1}:good.txt" % ref).stdout,
                             "the superseded snapshot is unreachable: no reflog kept it")

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs enforced file modes")
    def test_a_tick_on_unwritable_storage_exits_clean_and_silent(self):
        """The script's own header says every path exits 0, always.

        A wait stamp was written with `: > "$f" 2>/dev/null`. `:` is a POSIX
        SPECIAL BUILTIN, so a redirection error there is FATAL to the shell:
        on a telemetry directory that is unwritable or cannot be created, the
        tick died at that statement, before the lock, before the counter and
        before any log line, printing a raw shell diagnostic and exiting 1.
        Twelve consecutive ticks gave twelve exit 1s, zero log lines and zero
        snapshots, out of the tool whose whole job is that a crash never
        costs work.

        Both reachable shapes are driven, and the assertion is on the promise
        rather than on the statement that broke it: exit 0, nothing on stderr,
        and a writable vault still counting and still snapshotting.
        """
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            self._repo(repo)
            vault = tempfile.mkdtemp()
            tel = os.path.join(vault, "99-System", "telemetry")
            os.makedirs(tel)
            parent = tempfile.mkdtemp()
            os.chmod(tel, 0o555)
            os.chmod(parent, 0o555)
            try:
                shapes = {"unwritable telemetry directory": vault,
                          "vault that cannot be created": os.path.join(parent, "vault")}
                for why, v in shapes.items():
                    env = dict(os.environ, BROTHERSBE_VAULT=v, BROTHERSBE_AUTOSAVE="1")
                    for _ in range(3):
                        out = subprocess.run([sys.executable, hook, "tick", "sessA"],
                                             input=json.dumps({"cwd": repo}), text=True,
                                             capture_output=True, env=env, timeout=120)
                        self.assertEqual(0, out.returncode,
                                         "a tick on an %s exited %d; the header promises every "
                                         "path exits 0" % (why, out.returncode))
                        self.assertEqual("", out.stderr.strip(),
                                         "a tick on an %s printed a raw diagnostic: %r"
                                         % (why, out.stderr[:160]))
            finally:
                os.chmod(tel, 0o755)
                os.chmod(parent, 0o755)
            # The control, so this is not a script that has learned to do
            # nothing: a writable vault still counts and still snapshots.
            good = tempfile.mkdtemp()
            io.open(os.path.join(repo, "unlanded.txt"), "w").write("WIP-WORK")
            env = dict(os.environ, BROTHERSBE_VAULT=good, BROTHERSBE_AUTOSAVE="1",
                       BROTHERSBE_AUTOSAVE_EVERY="2")
            for _ in range(2):
                subprocess.run([sys.executable, hook, "tick", "sessB"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; the counter and the ref are asserted below
                               text=True, capture_output=True, env=env, timeout=120)
            gtel = os.path.join(good, "99-System", "telemetry")
            ctr = [f for f in os.listdir(gtel) if f.startswith(".autosave-tick")
                   and not f.endswith((".lock", ".warned"))]
            self.assertEqual(["2"], [io.open(os.path.join(gtel, c)).read() for c in ctr],
                             "a writable vault stopped counting")
            refs = subprocess.run(["git", "-C", repo, "for-each-ref",
                                   "refs/brothersbe/autosave"],
                                  capture_output=True, text=True).stdout.split("\n")
            self.assertTrue([r for r in refs if r.strip()],
                            "a writable vault took no snapshot at the throttle point")

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs enforced file modes")
    def test_an_unwritable_vault_still_lands_the_reason_in_a_fallback_log(self):
        """review-13a: exit 0 was already the promise being kept (the prior
        fix covers that), but the REASON was not. log_line's and
        excl_record's own `mkdir -p "$TEL_DIR"` failed the same way the write
        it was about to explain failed, so a skipped precompact or tick left
        no trace anywhere: not in autosave.log, not in
        autosave-exclusions.log, not on stderr (kept clean on purpose). The
        fallback lands the same reason beside the repository's own git
        metadata, which does not depend on the vault at all."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            self._repo(repo)
            io.open(os.path.join(repo, "unlanded.txt"), "w").write("WIP-WORK")
            vault = tempfile.mkdtemp()
            tel = os.path.join(vault, "99-System", "telemetry")
            os.makedirs(tel)
            os.chmod(tel, 0o555)
            fb = os.path.join(repo, ".git", "brothersbe-autosave-fallback.log")
            try:
                env = dict(os.environ, BROTHERSBE_VAULT=vault)
                out = subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),
                                     text=True, capture_output=True, env=env, timeout=120,
                                     cwd=repo)
                self.assertEqual(0, out.returncode,
                                 "precompact on an unwritable vault exited %d" % out.returncode)
                self.assertEqual("", out.stderr.strip(),
                                 "precompact on an unwritable vault printed a raw diagnostic: %r"
                                 % out.stderr[:160])
                self.assertTrue(os.path.exists(fb),
                                "an unwritable vault left no trace anywhere: no fallback log at %s"
                                % fb)
                body = io.open(fb).read()
                self.assertIn("vault unwritable", body)
                self.assertIn("saved ", body,
                              "the fallback log exists but does not carry the reason that would "
                              "otherwise have gone to autosave.log")
            finally:
                os.chmod(tel, 0o755)
            # The control: a writable vault needs no fallback and writes none.
            good_repo = tempfile.mkdtemp()
            self._repo(good_repo)
            io.open(os.path.join(good_repo, "unlanded.txt"), "w").write("WIP-WORK")
            good_vault = tempfile.mkdtemp()
            env2 = dict(os.environ, BROTHERSBE_VAULT=good_vault)
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": good_repo}),  # sbe: allow-silent test harness fires the hook; the fallback's absence is asserted below
                           text=True, env=env2, timeout=120, cwd=good_repo)
            good_fb = os.path.join(good_repo, ".git", "brothersbe-autosave-fallback.log")
            self.assertFalse(os.path.exists(good_fb),
                             "a writable vault still wrote a fallback log nobody needed: %s"
                             % good_fb)
            self.assertTrue(os.path.exists(os.path.join(good_vault, "99-System", "telemetry",
                                                         "autosave.log")),
                            "the control run did not use the real vault log either")

    def test_recover_after_a_rename_names_the_sibling_snapshots(self):
        """The ref id derives from the worktree path, so a moved project
        changes the id and recover looked in a ref that never existed,
        printing 'no autosave found in <repo>' about a repository holding
        the snapshot one for-each-ref away. The empty-ref branch now
        enumerates the namespace and names what it found."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as parent:
            repo = os.path.join(parent, "proj")
            os.makedirs(repo)
            git = self._repo(repo)
            io.open(os.path.join(repo, "wip.txt"), "w").write("UNLANDED")
            vault = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; recover's output is asserted below
                           text=True, env=env, timeout=120)
            renamed = os.path.join(parent, "proj-renamed")
            os.rename(repo, renamed)
            out = subprocess.run([sys.executable, hook, "recover", renamed], capture_output=True,
                                 text=True, env=env, timeout=120).stdout
            self.assertIn("DOES hold autosave snapshot(s) under other id(s)", out)
            self.assertIn("refs/brothersbe/autosave/", out)
            self.assertNotIn("no autosave found in", out,
                             "recover still asserts a repo-wide absence it never examined")


class TestTelemetryWriterSerialization(unittest.TestCase):
    def test_a_live_append_survives_a_concurrent_migrate(self):
        """Two REAL writers, racing. cmd_migrate is a read-modify-write over
        the whole ledger and there was no lock anywhere in the repository, so
        a row appended between its read and its rename was destroyed by the
        rename while the loss guard compared two pre-append counts and
        printed "count ok". Writers now serialize on an exclusive flock and
        the guard recounts the real post-rename file under that lock."""
        with tempfile.TemporaryDirectory() as vault:
            tel = os.path.join(vault, "99-System", "telemetry")
            os.makedirs(tel)
            led = os.path.join(tel, "outcomes.jsonl")
            with io.open(led, "w") as f:
                for i in range(120000):
                    f.write(json.dumps({"session_id": "s%d" % i, "out_tokens": i}) + "\n")
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            mig = subprocess.Popen(
                [sys.executable, os.path.join(HERE, "sbe_telemetry.py"), "migrate"],
                env=env, stdout=subprocess.PIPE, text=True)
            appenders = [subprocess.Popen(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, %r); import sbe_telemetry as t; "
                 "t.atomic_append(t.LEDGER, {'schema': 2, 'session_id': 'LIVE-%d'})" % (HERE, i)],
                env=env) for i in range(4)]
            out, _ = mig.communicate(timeout=300)
            for a in appenders:
                a.wait(timeout=300)
            body = io.open(led, errors="replace").read()
            for i in range(4):
                self.assertIn("LIVE-%d" % i, body,
                              "a live session's appended row was destroyed by migrate")
            self.assertIn("count ok", out, "migrate did not finish cleanly: %r" % out.strip())
            self.assertEqual(120004, sum(1 for l in body.splitlines() if l.strip()))

    def test_two_concurrent_migrates_both_finish_and_the_ledger_is_migrated(self):
        """The two maintenance commands used to share one literal .tmp path,
        so one renamed the other's half-written file out from under it and
        the loser died with a bare FileNotFoundError; racing migrate against
        dedup could leave a ledger with ZERO schema-2 rows under a printed
        "migrated to schema 2, count ok". Serialized writers and per-process
        temp paths: both commands finish, and the migration reaches the file."""
        with tempfile.TemporaryDirectory() as vault:
            tel = os.path.join(vault, "99-System", "telemetry")
            os.makedirs(tel)
            led = os.path.join(tel, "outcomes.jsonl")
            with io.open(led, "w") as f:
                for i in range(60000):
                    f.write(json.dumps({"session_id": "s%d" % i, "out_tokens": i}) + "\n")
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            runs = [subprocess.Popen(
                [sys.executable, os.path.join(HERE, "sbe_telemetry.py"), cmd],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for cmd in ("migrate", "dedup")]
            outs = [p.communicate(timeout=300)[0] for p in runs]
            for o in outs:
                self.assertNotIn("FileNotFoundError", o,
                                 "a maintenance command lost its temp file to the other")
            schemas = set()
            for l in io.open(led, errors="replace"):
                schemas.add(json.loads(l).get("schema"))
            self.assertEqual({2}, schemas,
                             "the printed migration never reached the file: %r" % schemas)

    def _telemetry(self, vault):
        import importlib
        os.environ["BROTHERSBE_VAULT"] = vault
        sys.path.insert(0, HERE)
        import sbe_telemetry
        importlib.reload(sbe_telemetry)
        return sbe_telemetry

    def test_a_fallback_append_during_the_rewrite_window_is_carried_not_destroyed(self):
        """The round-12 closure of this class was FALSE at scale: the recount
        ran AFTER the rename, so it compared the rewrite to itself, and a row
        appended by the 15-second unlocked fallback was destroyed under a
        printed "count ok" whenever the rewrite outlived the timeout. The
        freshness guard now runs BEFORE the rename against the live file and
        carries appended bytes into the rewrite. This test walks the exact
        window: the tail row exists on disk beyond what the rewrite read."""
        with tempfile.TemporaryDirectory() as vault:
            t = self._telemetry(vault)
            os.makedirs(t.TEL_DIR, exist_ok=True)
            with io.open(t.LEDGER, "w") as f:
                f.write('{"session_id":"a"}\n{"session_id":"b"}\n')
                read_size = f.tell()
                f.write('{"session_id":"LIVE-FALLBACK-ROW"}\n')  # appended after the read
            done = t._rewrite_locked(t.LEDGER, ['{"session_id":"a","schema":2}',
                                                '{"session_id":"b","schema":2}'],
                                     read_size, "migrate")
            self.assertIsNotNone(done, "the rewrite refused a carryable tail")
            n_after, passthrough, count_ok = done
            self.assertEqual((3, 1, True), (n_after, passthrough, count_ok))
            body = io.open(t.LEDGER).read()
            self.assertIn("LIVE-FALLBACK-ROW", body,
                          "the fallback append was destroyed by the rename")

    def test_a_file_that_shrinks_under_the_lock_is_never_replaced(self):
        """A file smaller than what the rewrite read means a second writer is
        ignoring the lock; renaming over it would replace a world this
        rewrite never saw, so it refuses and leaves everything in place."""
        with tempfile.TemporaryDirectory() as vault:
            t = self._telemetry(vault)
            os.makedirs(t.TEL_DIR, exist_ok=True)
            original = '{"session_id":"a"}\n'
            with io.open(t.LEDGER, "w") as f:
                f.write(original)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                done = t._rewrite_locked(t.LEDGER, ['{"x":1}'],
                                         len(original) + 50, "migrate")
            said = buf.getvalue()
            self.assertIsNone(done, "a shrunken file was replaced anyway")
            self.assertIn("SHRANK", said, "the refusal never told the operator why")
            self.assertIn("19 on disk", said,
                          "the refusal did not name what it measured")
            self.assertEqual(original, io.open(t.LEDGER).read(), "the refusal still wrote")
            self.assertEqual([], [p for p in os.listdir(t.TEL_DIR) if ".tmp." in p],
                             "the refusal leaked its temp file")

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs enforced file modes")
    def test_an_unopenable_lock_sidecar_never_drops_the_row(self):
        """The sidecar exists only to protect the row, and its open failure
        used to raise into the top-level swallow: the row was lost, the
        operator got a Python repr, and the exit code was 0. Failing to OPEN
        the lock now takes the same path as failing to TAKE it."""
        with tempfile.TemporaryDirectory() as vault:
            t = self._telemetry(vault)
            os.makedirs(t.TEL_DIR, exist_ok=True)
            unwritable = t.LEDGER + ".lock"
            io.open(unwritable, "w").close()
            os.chmod(unwritable, 0o444)
            t.atomic_append(t.LEDGER, {"session_id": "row-behind-a-bad-lock"})
            os.mkdir(t.CORRECTIONS + ".lock")     # the mkdir-shadowed shape
            t.atomic_append(t.CORRECTIONS, {"session_id": "row-behind-a-dir-lock"})
            self.assertIn("row-behind-a-bad-lock", io.open(t.LEDGER).read())
            self.assertIn("row-behind-a-dir-lock", io.open(t.CORRECTIONS).read())

    def test_short_writes_are_completed_not_truncated(self):
        """os.write may honor fewer bytes than asked on any POSIX host; the
        unchecked call truncated the row silently. _write_all loops."""
        with tempfile.TemporaryDirectory() as vault:
            t = self._telemetry(vault)
            os.makedirs(t.TEL_DIR, exist_ok=True)
            real_write = os.write
            os.write = lambda fd, data: real_write(fd, bytes(data)[:5])
            try:
                t.atomic_append(t.LEDGER, {"session_id": "short-write-probe", "n": 12345})
            finally:
                os.write = real_write
            row = json.loads(io.open(t.LEDGER).read())
            self.assertEqual({"session_id": "short-write-probe", "n": 12345}, row)

    def test_an_intent_record_stays_one_line(self):
        """The intent log is a line-delimited RECORD whose line structure is
        its parse: a caller newline used to write a second timestamped record
        the tool never stamped, and the last-line reader quoted the forged
        record as the operator's own intent. Every internal break renders as
        a visible escape."""
        with tempfile.TemporaryDirectory() as vault:
            t = self._telemetry(vault)
            path = os.path.join(t.TEL_DIR, "intent-test.log")
            t.atomic_append_text(path, "one\n2026-01-01T00:00:00Z  next: FORGED")
            lines = io.open(path).read().splitlines()
            self.assertEqual(1, len(lines), "a caller newline split the record: %r" % lines)
            self.assertIn("\\n", lines[0], "the break vanished instead of rendering visibly")


class TestWriterLockByteRangeCalibration(unittest.TestCase):
    """Windows-port round 2, finding 1: fcntl.flock, the only lock path this
    machine runs, never touches a byte range, so round 1's msvcrt.locking
    reset (os.lseek before LOCK) shipped untestable. Loads a TEMP COPY of
    sbe_telemetry.py, fcntl forced None, msvcrt replaced by a fake recording
    the fd's real position at every locking() call: asserts position 0 for
    both calls on the real source, then that a SCRATCH mutant with the
    reset deleted goes red. A second reset once before UNLOCK could not go
    red the same way (nothing moves the fd between lock and unlock), so it
    was dead code, deleted."""
    REAL = os.path.join(HERE, "sbe_telemetry.py")
    LSEEK_RESET_LINE = "os.lseek(fd, 0, os.SEEK_SET)"

    class _PositionRecordingMsvcrt(object):
        """Never raises, never itself moves the fd, so only the CODE UNDER
        TEST can. calls holds (label, position, nbytes): three values, not a
        (verdict, evidence) pair the honesty meta-test could mistake this for."""

        LK_NBLCK, LK_UNLCK = 2, 0

        def __init__(self):
            self.calls = []

        def locking(self, fd, mode, nbytes):
            pos = os.lseek(fd, 0, os.SEEK_CUR)  # a peek; never moves the fd
            label = "LOCK" if mode == self.LK_NBLCK else "UNLOCK"
            self.calls.append((label, pos, nbytes))

    def _run_writer_lock_once(self, source_text):
        """source_text as a throwaway module in a scratch dir, fcntl forced
        absent, msvcrt replaced by the fake; runs one _writer_lock
        acquire/release against a fresh sidecar in that same scratch dir.
        Returns (held, calls, sidecar), never a bare 2-value pair."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sbe_telemetry_lockcal.py")
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(source_text)
            spec = importlib.util.spec_from_file_location("sbe_telemetry_lockcal", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.fcntl = None
            fake = self._PositionRecordingMsvcrt()
            mod.msvcrt = fake
            sidecar = os.path.join(d, "outcomes.jsonl")
            with mod._writer_lock(sidecar) as held:
                pass
        return held, fake.calls, sidecar

    def test_the_pre_lock_reset_is_load_bearing_at_byte_range_zero(self):
        with io.open(self.REAL, encoding="utf-8") as f:
            original = f.read()
        n = original.count(self.LSEEK_RESET_LINE)
        self.assertEqual(1, n, "expected one %r, found %d; the mutation below "
                         "assumes one" % (self.LSEEK_RESET_LINE, n))
        held, calls, sidecar = self._run_writer_lock_once(original)
        self.assertTrue(held, "the fake lock was never granted: calls=%r" % (calls,))
        self.assertEqual(["LOCK", "UNLOCK"], [c[0] for c in calls],
                         "expected one LOCK call then one UNLOCK call: %r" % (calls,))
        for label, pos, nbytes in calls:
            self.assertEqual(0, pos, "%s at fd position %d, not 0 (two openers of "
                             "a fresh 0-byte sidecar would lock different ranges "
                             "and never contend): calls=%r" % (label, pos, calls))

        # Calibration: strip the reset from a SCRATCH COPY, never the real file.
        lines = original.splitlines(keepends=True)
        mutant = "".join(l for l in lines if l.strip() != self.LSEEK_RESET_LINE)
        self.assertEqual(len(lines) - 1, len(mutant.splitlines()), "expected one line gone")
        held, calls, sidecar = self._run_writer_lock_once(mutant)
        self.assertTrue(held, "mutant still failed to lock: calls=%r" % (calls,))
        off_zero = [c for c in calls if c[1] != 0]
        self.assertTrue(off_zero, "deleting the pre-LOCK reset moved nothing off "
                        "position 0, the exact mutation round 1's harness missed: "
                        "calls=%r" % (calls,))
        self.assertEqual(("LOCK", 1), (off_zero[0][0], off_zero[0][1]),
                         "expected the padded sidecar's LOCK to drift to position 1 "
                         "(the pad byte this fd just wrote): %r" % (off_zero,))
        with io.open(self.REAL, encoding="utf-8") as f:  # restoration proof
            self.assertEqual(original, f.read(), "the real file changed during this test")


class TestWriterLockContentionCalibration(unittest.TestCase):
    """Windows-port round 2, finding 3: the byte-range fixture above never
    makes locking() raise, so no committed test ever put two openers into
    real contention or exercised the retry loop and the timeout refusal
    migrate/dedup print ("could not take or open the writer lock... within
    its timeout"). Loads a TEMP COPY of sbe_telemetry.py (as above), fcntl
    forced None, msvcrt replaced by a range-aware fake that raises OSError
    from locking() when the [pos, pos+n) range a caller wants is already
    held by a DIFFERENT fd, the shape a second migrate/dedup invocation
    meets on Windows while the first is still mid-rewrite. Opener A takes
    the lock and holds it; opener B, given a short timeout, must come back
    unheld only AFTER its own retry loop has actually attempted the lock
    more than once, not on the first failure. A SCRATCH mutant that turns
    the retry's sleep into an immediate break collapses B to one attempt
    and the fixture goes red."""
    REAL = os.path.join(HERE, "sbe_telemetry.py")
    RETRY_SLEEP_LINE = "time.sleep(0.05)"

    class _ContentionMsvcrt(object):
        """Range-aware fake: LK_NBLCK raises OSError when [pos, pos+n) is
        already held by a fd other than the caller's; LK_UNLCK releases
        whatever the caller's own fd holds there. Owner tracked by fd
        identity, since two _writer_lock() calls in one process open two
        distinct real fds on the same sidecar path, the same shape two
        separate migrate/dedup processes produce on the real file. calls
        holds (label, fd, position): three values, never a bare pair."""

        LK_NBLCK, LK_UNLCK = 2, 0

        def __init__(self):
            self.held_by = {}   # (position, nbytes) -> owner fd
            self.calls = []

        def locking(self, fd, mode, nbytes):
            pos = os.lseek(fd, 0, os.SEEK_CUR)  # a peek; never moves the fd
            key = (pos, nbytes)
            label = "LOCK" if mode == self.LK_NBLCK else "UNLOCK"
            self.calls.append((label, fd, pos))
            if mode == self.LK_NBLCK:
                owner = self.held_by.get(key)
                if owner is not None and owner != fd:
                    raise OSError(36, "Resource deadlock avoided")
                self.held_by[key] = fd
            else:
                self.held_by.pop(key, None)

    def _run_two_openers(self, source_text, timeout_b):
        """source_text as a throwaway module, fcntl forced absent, msvcrt
        replaced by the contention-raising fake. Opener A takes the lock
        with a long timeout and holds it; opener B attempts the SAME
        sidecar with timeout_b while A still holds, then both release.
        Returns (held_a, held_b, calls_made_by_b), never a bare pair."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sbe_telemetry_lockcal2.py")
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(source_text)
            spec = importlib.util.spec_from_file_location("sbe_telemetry_lockcal2", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.fcntl = None
            fake = self._ContentionMsvcrt()
            mod.msvcrt = fake
            sidecar = os.path.join(d, "outcomes.jsonl")

            a_cm = mod._writer_lock(sidecar, timeout_s=5.0)
            held_a = a_cm.__enter__()
            before = len(fake.calls)
            b_cm = mod._writer_lock(sidecar, timeout_s=timeout_b)
            held_b = b_cm.__enter__()
            calls_b = fake.calls[before:]
            b_cm.__exit__(None, None, None)
            a_cm.__exit__(None, None, None)
        return held_a, held_b, calls_b

    def test_the_second_opener_is_refused_only_after_retrying(self):
        with io.open(self.REAL, encoding="utf-8") as f:
            original = f.read()
        n = original.count(self.RETRY_SLEEP_LINE)
        self.assertEqual(1, n, "expected one %r, found %d; the mutation below "
                         "assumes one" % (self.RETRY_SLEEP_LINE, n))

        held_a, held_b, calls_b = self._run_two_openers(original, timeout_b=0.3)
        self.assertTrue(held_a, "opener A never got the lock, so B was never "
                         "actually contending for it")
        self.assertFalse(held_b, "opener B acquired the lock while A still held "
                         "it: calls=%r" % (calls_b,))
        lock_calls_b = [c for c in calls_b if c[0] == "LOCK"]
        self.assertGreaterEqual(len(lock_calls_b), 2,
                                 "expected the retry loop to attempt the lock "
                                 "more than once before B's timeout gave up "
                                 "(a single attempt is not contention proven "
                                 "over time, it is a coin flip): calls=%r" % (calls_b,))

        # Calibration: turn the retry's pause into an immediate give-up in a
        # SCRATCH COPY, never the real file.
        mutant = original.replace(self.RETRY_SLEEP_LINE, "break", 1)
        self.assertEqual(0, mutant.count(self.RETRY_SLEEP_LINE),
                         "expected the retry sleep line gone from the mutant")
        held_a2, held_b2, calls_b2 = self._run_two_openers(mutant, timeout_b=0.3)
        self.assertTrue(held_a2, "opener A never got the lock under the mutant "
                         "either, so B still was not actually contending")
        self.assertFalse(held_b2, "even the broken retry loop refuses on the "
                         "first failure, so B is still unheld: calls=%r" % (calls_b2,))
        lock_calls_b2 = [c for c in calls_b2 if c[0] == "LOCK"]
        self.assertEqual(1, len(lock_calls_b2),
                         "breaking the retry loop should collapse B to exactly "
                         "one attempt instead of genuinely retrying, the exact "
                         "defect this fixture exists to catch: calls=%r" % (calls_b2,))
        with io.open(self.REAL, encoding="utf-8") as f:  # restoration proof
            self.assertEqual(original, f.read(), "the real file changed during this test")


class TestDigestCap(unittest.TestCase):
    def test_digest_fits_the_cap_the_hook_comment_names(self):
        """sbe_sessionstart.py injects DIGEST.md into session context and its
        own comment names the injection cap. The digest once grew to 16 KB
        while that comment kept promising "we stay far under", so the file
        making the claim contradicted the file it claimed about. This test
        reads the cap out of the hook comment instead of hardcoding a second
        number, so the two cannot disagree again. It does not verify the
        harness's real cap: that figure is the hook comment's own claim."""
        hook = io.open(os.path.join(HERE, "sbe_sessionstart.py")).read()
        m = re.search(r"(\d+)k char cap", hook)
        self.assertTrue(m, "hook comment no longer names its injection cap")
        cap = int(m.group(1)) * 1000
        size = os.path.getsize(os.path.join(HERE, "..", "DIGEST.md"))
        self.assertLess(size, cap,
                        "DIGEST.md is %d bytes but the hook comment promises a %d cap; "
                        "move the growth into LAWS-REFERENCE.md" % (size, cap))
        # The cap was guarded for the digest FILE only, while the hook appended
        # nags and hints on top, so a busy vault could push the total past the
        # cap and the harness truncated the tail, where the compaction hint
        # (the recovery pointer) printed. The hook now enforces the cap itself
        # and prints the hint before the digest; both properties are pinned
        # against the script's text here.
        self.assertRegex(hook, r"CAP\s*=\s*%d" % cap,
                         "the hook no longer enforces the cap it names")
        for cut in ("dig_b[:keep]", "head_b[:CAP - MARKER_ROOM]"):
            self.assertIn(cut, hook, "the hook no longer cuts its own over-cap output")
        hint_at = hook.find("compact-hint")
        digest_at = hook.find('os.path.join(DIR, "DIGEST.md")')
        self.assertTrue(0 < hint_at < digest_at,
                        "the compaction hint must print before the digest, so harness "
                        "truncation can only ever cost digest tail")
        # The injected block says which version it came from, and that claim
        # tracks the VERSION file rather than a human's memory at cut time.
        version = io.open(os.path.join(HERE, "..", "VERSION")).read().strip()
        digest_head = io.open(os.path.join(HERE, "..", "DIGEST.md")).readline()
        self.assertIn("version %s" % version, digest_head,
                      "DIGEST.md header does not name the version in VERSION (%s)" % version)

    def test_the_law_file_stays_under_its_own_named_ceiling(self):
        """SKILL.md names a byte ceiling for itself in the What-is-not-law
        section, because the law file is the document most able to grow past
        the point where anyone reads it. The ceiling is read out of the text,
        not hardcoded here, so the claim and the assert cannot disagree; a law
        merges with or displaces an existing one rather than accreting."""
        body = io.open(os.path.join(HERE, "..", "SKILL.md")).read()
        m = re.search(r"SKILL\.md\s+stays under ([\d,]+) bytes", body)
        self.assertTrue(m, "SKILL.md no longer names its own byte ceiling")
        ceiling = int(m.group(1).replace(",", ""))
        size = os.path.getsize(os.path.join(HERE, "..", "SKILL.md"))
        self.assertLess(size, ceiling,
                        "SKILL.md is %d bytes, past its own %d ceiling; merge or displace "
                        "a law instead of accreting" % (size, ceiling))



    def test_the_truncation_marker_names_what_was_actually_cut(self):
        """The marker used to ASSERT which half was cut, and nothing bounded the
        first half: a large resume brief (written from a transcript, so its size
        is data-driven) pushed the hint past the cap, and the printed line still
        said the digest tail was cut and the hint printed in full. Both clauses
        were false in that run, in the one line whose job is to explain a
        context loss. This runs the hook for real in both regimes and reads the
        marker against what actually printed."""
        hook = os.path.join(HERE, "sbe_sessionstart.py")
        cap = int(re.search(r"CAP\s*=\s*(\d+)", io.open(hook).read()).group(1))
        work = tempfile.mkdtemp()
        try:
            # The REAL hook and the real DIGEST.md (the hook derives its own
            # directory), pointed at a temp vault: read-only against this tree,
            # and the brief that overflows is written into the temp vault.
            root = os.path.abspath(os.path.join(HERE, ".."))
            vault = os.path.join(work, "vault")
            os.makedirs(os.path.join(vault, "99-System", "telemetry"))
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            cwd = os.path.join(work, "proj")
            os.makedirs(cwd)
            payload = json.dumps({"source": "compact", "cwd": cwd})
            hook_path = os.path.join(root, "tools", "sbe_sessionstart.py")
            digest_head = io.open(os.path.join(root, "DIGEST.md")).readline().strip()

            def run():
                r = subprocess.run([sys.executable, hook_path], input=payload, capture_output=True,
                                   text=True, env=env, timeout=180)
                self.assertEqual(r.returncode, 0, "the hook must always exit 0")
                self.assertLessEqual(len(r.stdout.encode("utf-8")), cap + 400,
                                     "the hook did not enforce the cap it names")
                return r.stdout

            # Regime 1: ordinary run, nothing cut, so no marker prints at all.
            out = run()
            self.assertNotIn("truncated at the", out,
                             "an ordinary run must not claim it was truncated")

            # Regime 2: a resume brief far larger than the whole cap. This is
            # the run where the old marker named the wrong casualty: the digest
            # does not print AT ALL and the hint itself is cut.
            spec_t = importlib.util.spec_from_file_location(
                "sbe_telemetry_cap", os.path.join(root, "tools", "sbe_telemetry.py"))
            tel = importlib.util.module_from_spec(spec_t)
            os.environ["BROTHERSBE_VAULT"] = vault
            spec_t.loader.exec_module(tel)
            io.open(tel._resume_path(cwd), "w").write("RESUME BRIEF\n" + ("x " * cap * 2) + "\n")
            out = run()
            self.assertIn("truncated at the", out, "an over-cap run must say so")
            printed_digest = digest_head and digest_head in out
            claims_digest_printed = "The compaction hint and nags printed in full" in out
            self.assertEqual(bool(claims_digest_printed), bool(printed_digest),
                             "the truncation marker's claim about which half survived "
                             "disagrees with the output:\n%s" % out[-500:])
            self.assertIn("the digest did not print at all", out,
                          "the marker must name the digest as the whole casualty when the "
                          "hint alone overflows:\n%s" % out[-500:])
        finally:
            shutil.rmtree(work, ignore_errors=True)


class TestDigestBudget(unittest.TestCase):
    def test_digest_stays_under_the_shrunk_byte_ceiling(self):
        """DIGEST.md is injected into EVERY session before the user asks for
        anything, unlike the 10k-char hook cap TestDigestCap enforces (that
        cap is the harness's outer bound, not a target). A prior release let
        the digest grow to 9025 bytes, 24 law lines deep, which measured as
        roughly 2200-2300 tokens of unconditional startup cost. The fix moved
        everything except the three unconditional laws (L6, L11, L14), the
        checked/human legend, the NO-DATA statement, and the
        after-compaction-or-resume pointer out to
        references/laws-full-digest.md, verbatim. This test pins the digest
        under DIGEST_BYTE_CEILING mechanically, so that growth cannot creep
        back in silently the way it did before."""
        size = os.path.getsize(os.path.join(HERE, "..", "DIGEST.md"))
        self.assertLess(size, DIGEST_BYTE_CEILING,
                        "DIGEST.md is %d bytes, at or past the %d-byte budget ceiling "
                        "chosen to keep startup injection under the token target; move "
                        "growth into references/laws-full-digest.md instead of "
                        "accreting the unconditional digest" % (size, DIGEST_BYTE_CEILING))

    def test_digest_moved_lines_survive_verbatim_in_references(self):
        """Every law line the digest no longer prints unconditionally must
        still exist, unabridged, in references/laws-full-digest.md, so
        shrinking the digest deferred content instead of dropping it. This
        spot-checks a handful of the moved lines for their exact original
        text rather than trusting a paraphrase."""
        moved = io.open(os.path.join(HERE, "..", "references",
                                      "laws-full-digest.md"), encoding="utf-8").read()
        must_contain = [
            "Install the check BEFORE writing the work. [human]",
            "L1 tier before work: five intake answers to 00-intake.json, first "
            "match wins, malformed answers refused by name.",
            "L13 one writer per file: fence then dispatch, in your registry, "
            "tier-tagged, closed with an inline evidence block.",
            "L19 a review verdict counts only when it names the falsification "
            "actually executed",
            "Unverified output carries the label UNVERIFIED next to the item, "
            "never in a footnote. [human]",
            "Every shipped threshold and RUBRIC baseline was measured on one "
            "estate. Re-measure on yours. [human]",
        ]
        for snippet in must_contain:
            self.assertIn(snippet, moved,
                          "a moved law line lost exact text: %r not found "
                          "verbatim in laws-full-digest.md" % snippet)

    def test_digest_still_carries_its_unconditional_survivors(self):
        """L6, L11 and L14 are the three laws SKILL.md itself calls
        unconditional, plus the checked/human legend and the
        after-compaction-or-resume pointer are the safety floor: whatever
        else moves out of the digest, these must not."""
        digest = io.open(os.path.join(HERE, "..", "DIGEST.md"), encoding="utf-8").read()
        must_contain = [
            "L6 stop immediately on any forcing condition",
            "L11 silent-failure lints",
            "L14 blast radius: no apply rights on production state",
            "NO-DATA is never a pass",
            "never a block",
            "[checked: tool] means a script decides it and CI can block on it. "
            "[human] means nothing computes it, and the line is a stated "
            "discipline, not a control.",
            "After compaction or resume: re-read SKILL.md",
            "references/laws-full-digest.md",
        ]
        for snippet in must_contain:
            self.assertIn(snippet, digest,
                          "the shrunk digest lost a required survivor: %r" % snippet)


def _zero_network_scan_paths(root, here):
    """The exact file set the zero-network AST scan walks, parametrized by
    (root, here) so TestGuiNetworkAllowlistIsNarrow below can run the SAME
    globbing logic against a throwaway scratch copy instead of a
    reimplementation that could silently drift from the real scan. Against
    the real repository, `here` is HERE (tools/) and `root` is its parent."""
    return (
        glob.glob(os.path.join(here, "*.py")) +
        glob.glob(os.path.join(root, "src", "brothersbe", "*.py")) +
        # Walked recursively (not skipped, not left to the top-level glob
        # above) so any file under gui/ other than the one allow-listed
        # server.py stays subject to the same ban as everything else. See
        # docs/adr/2026-08-05-gui-server-amendment.md.
        glob.glob(os.path.join(root, "src", "brothersbe", "gui", "**", "*.py"), recursive=True) +
        glob.glob(os.path.join(root, "hooks", "**", "*.py"), recursive=True) +
        glob.glob(os.path.join(root, "scripts", "**", "*.py"), recursive=True) +
        [os.path.join(root, "bin", "sbe")]
    )


def _zero_network_allowlist(root):
    """The zero-network scan's exact-path exceptions. src/brothersbe/gui/server.py
    is reserved by the 2026-08-05 amendment
    (docs/adr/2026-08-05-gui-server-amendment.md, gate LP-0301) and does not
    exist in the real repository yet; listing it here is inert until it
    does. Any OTHER file under src/brothersbe/gui/ is NOT in this set and
    stays banned, which is what TestGuiNetworkAllowlistIsNarrow exercises.
    Every path here must also appear as its own structured line in
    SECURITY.md's "Network exceptions, exact path only" section; see
    _path_is_documented_exception below, which both halves of this scan's
    membership test (Python and shell) share."""
    return {
        os.path.join(root, "src", "brothersbe", "prverify.py"),
        # `sbe pr verify`'s Bitbucket client, the sibling of prverify.py above
        # and permitted on exactly the same terms: one documented API client,
        # named by exact path so no other module can hide behind a directory.
        # Both are read-only, both refuse to open a socket without an explicit
        # credential, and both report NO-DATA naming the reason rather than
        # passing when they cannot look. SECURITY.md and docs/KNOWN-LIMITS.md
        # carry the matching sentences.
        os.path.join(root, "src", "brothersbe", "bbprverify.py"),
        # The one WRITE client in the tree reached from Python (its shell-side
        # counterpart is scripts/local-gates.sh's own `gh api -X POST`, caught
        # by the shell half of this scan below): it posts the local gate
        # runner's verdict to Bitbucket's commit build-status resource, which
        # is what `gh api` does on the GitHub side. Named by exact path like
        # the others, and permitted on the same terms: no credential means
        # zero network attempts and a NO-DATA naming the remedy, and it
        # reports a verdict rather than reaching one, so nothing it does can
        # turn a red battery green.
        os.path.join(root, "src", "brothersbe", "bbstatus.py"),
        # `sbe protections verify`'s own `gh api` client: read-only by
        # construction (refuses any HTTP method other than GET before it
        # builds a request), no credential means zero requests, and it was
        # added to this allowlist and to SECURITY.md in the same change that
        # taught the Python half of this scan to see a `gh api` subprocess
        # call built into a local variable first (the shape this file uses),
        # not only a bare `import`; before that it was a real, undocumented
        # network entry point this scan could not see.
        os.path.join(root, "src", "brothersbe", "protections.py"),
        os.path.join(root, "src", "brothersbe", "gui", "server.py"),
    }


def _banned_import_violations(py_files, allowlisted, banned=("urllib", "requests", "socket", "http")):
    """AST-parse each file in py_files (skipping anything in allowlisted or
    missing on disk) and return a list of (path, [banned modules imported])
    for every violation found. Shared by the real zero-network scan and its
    adversarial gui/ regression test so both exercise identical logic rather
    than two copies that could drift apart."""
    banned = set(banned)
    violations = []
    for p in py_files:
        if p in allowlisted or not os.path.exists(p):
            continue
        tree = ast.parse(io.open(p, errors="replace").read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            hit = sorted(set(mods) & banned)
            if hit:
                violations.append((p, hit))
    return violations


# Not curl/wget/nc alone: `gh` is a network client too, and both can be
# invoked from Python without ever writing `import urllib` -- the shape
# _banned_import_violations above cannot see. frozenset, never a list or
# tuple: this file is itself inside the surface _banned_subprocess_violations
# below scans, and a List/Tuple literal holding these exact words would trip
# its own check.
_PY_NETWORK_TOKENS = frozenset({"curl", "wget", "nc"})


def _banned_subprocess_violations(py_files, allowlisted):
    """AST-walk each file in py_files (skipping anything in allowlisted or
    missing on disk) for a List or Tuple literal that names a network client
    as a subprocess argument vector: curl, wget or nc directly, or `gh api`
    (`gh` as the first element, `api` present anywhere after it). Catches the
    list built into a local variable first (`argv = [...]; subprocess.run(
    argv)`, the shape src/brothersbe/protections.py's own `gh api` call uses)
    as well as one written inline (`subprocess.run(["curl", url])`, the
    reproduction that found this gap in the first place), because both are
    the same List AST node either way; this does not need to trace which
    Call the list eventually reaches. Returns (path, lineno, matched tokens)
    triples.

    Deliberately narrower than the shell scan below: it does not flag a bare
    `git clone`/`fetch`/`pull`/`push`/`ls-remote` argument vector. This
    tree's own Python test suite builds exactly that shape in three files
    (tools/test_sbe_golden_scenario.py, tools/test_sbe_handover.py,
    tools/test_sbe_install.py), each cloning a LOCAL tempdir fixture with
    `["git", "clone", "-q", <a variable>, <a variable>]`, never a remote --
    measured 2026-08-21 by widening this scan to include the git verbs and
    reading every hit it produced. A scan that cannot tell that fixture from
    a real remote clone would need either a false allowlist entry (naming a
    local-only test helper as a "real" network exception, which is exactly
    what SECURITY.md's Network exceptions section must never contain) or a
    same-file "is the source argument a real remote" heuristic that no case
    in this repository warrants, curl/wget/nc/`gh api` have no such
    local-only reading, which is why only they are banned here; git's shell
    counterpart below has no such fixture pattern to confuse it, so it does
    ban the git verbs."""
    violations = []
    for p in py_files:
        if p in allowlisted or not os.path.exists(p):
            continue
        tree = ast.parse(io.open(p, errors="replace").read())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            strs = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if not strs:
                continue
            hit = sorted(set(strs) & _PY_NETWORK_TOKENS)
            if not hit and strs[0] == "gh" and "api" in strs:
                hit = ["gh api"]
            if hit:
                violations.append((p, node.lineno, hit))
    return violations


def _zero_network_shell_paths(root, here):
    """The exact shell-file set the zero-network curl/wget/nc scan walks,
    parametrized by (root, here) for the same reason _zero_network_scan_paths
    above is: a mutation proof can run the SAME globbing logic against a
    scratch copy instead of a reimplementation that could silently drift from
    the real scan."""
    return (
        glob.glob(os.path.join(here, "*.sh")) +
        glob.glob(os.path.join(root, "hooks", "**", "*.sh"), recursive=True) +
        glob.glob(os.path.join(root, "scripts", "**", "*.sh"), recursive=True) +
        [os.path.join(root, "install.sh")]
    )


_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")


def _strip_shell_comment(line):
    """A `#` only starts a real shell comment where a new word could begin:
    start of line, or after whitespace. `${var#pattern}` and
    `${var##pattern}` are parameter expansions, not comments (install.sh's
    own `TARGET_ARG="${1#--target=}"` is the real case that found this): a
    naive `line.split("#", 1)[0]` truncates mid-string there, and once
    _shell_code_only below tracks quotes across the WHOLE file, that one
    dropped closing quote desyncs every line after it, turning a real
    network call anywhere later in the file invisible. This is the fix:
    only strip at a `#` preceded by whitespace or nothing."""
    m = _COMMENT_RE.search(line)
    return line[:m.start()] if m else line


def _shell_code_only(text):
    """Blank the contents of every quoted string (single or double),
    preserving newlines so line numbers stay correct, so a textual scan
    matches an actual command, not a sentence that mentions one: install.sh's
    own `--dry-run` message PRINTS the words "git clone $origin_url
    $clone_dest" to describe what it would do, and that must not read as
    running it. Call this AFTER _strip_shell_comment on every line and
    rejoining, never on raw text: a `#` that survives inside an
    already-open quote span is not a comment and must stay part of the
    string it is inside, which is exactly what stripping comments first
    (per physical line, matching real shell rules approximately) and only
    then tracking quotes across the joined result preserves. Not a real
    shell parser: does not handle variable expansion, command substitution,
    or a backslash-escaped quote inside a double-quoted string, an accepted
    gap for a textual scan (see this function's caller's docstring)."""
    out = []
    quote = None
    for ch in text:
        if quote:
            out.append("\n" if ch == "\n" else " ")
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


# frozenset, not a list/tuple: no self-match concern here (these words are
# not curl/wget/nc/gh/api), but consistent with _PY_NETWORK_TOKENS above.
_SHELL_GIT_NETWORK_VERBS = frozenset({"ls-remote", "fetch", "push", "clone", "pull"})


def _shell_network_hit(code):
    """True if this one line of CODE (comments and quoted strings already
    stripped by the two helpers above) invokes curl, wget, nc, `gh api`, or
    a git command that reaches a remote: ls-remote, fetch, push, clone or
    pull. Allows one flag(+value) between `git` and the verb -- the one real
    shape this tree uses, `git -C "$dest" pull --ff-only` in install.sh --
    by skipping tokens that start with "-" before checking whether the next
    one is a network verb; a token that is neither a flag nor a network verb
    ends the search for that `git`, so `git -C dir status` or any other
    local subcommand is never flagged."""
    if re.search(r"\b(curl|wget|nc)\b", code):
        return True
    if re.search(r"\bgh\s+api\b", code):
        return True
    tokens = code.split()
    for i, tok in enumerate(tokens):
        if tok != "git":
            continue
        for nxt in tokens[i + 1:i + 4]:
            if nxt in _SHELL_GIT_NETWORK_VERBS:
                return True
            if not nxt.startswith("-"):
                break
    return False


def _shell_network_violations(root, sh_files, allowlisted_rel):
    """Shell has no stdlib grammar to parse, so this scan stays textual, but
    it is no longer a bare substring match: strip each line's `#` comment
    (by the real shell rule _strip_shell_comment approximates), blank every
    quoted string across the rejoined file so prose describing a command is
    not mistaken for running it, then look for curl, wget, nc, a git remote
    operation, or `gh api` (_shell_network_hit above). Skips any file whose
    ROOT-relative, forward-slash path is in allowlisted_rel
    (TestAuditableSurface.SHELL_NETWORK_ALLOWLIST against the real tree).
    Returns a list of (relpath, line_number) violations. Parametrized like
    _banned_import_violations above so a mutation proof exercises this exact
    function against a scratch copy rather than a second copy of the logic."""
    violations = []
    for p in sh_files:
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if rel in allowlisted_rel or not os.path.exists(p):
            continue
        raw = io.open(p, errors="replace").read()
        no_comments = "\n".join(_strip_shell_comment(line) for line in raw.splitlines())
        blanked = _shell_code_only(no_comments)
        for i, code in enumerate(blanked.splitlines(), 1):
            if _shell_network_hit(code):
                violations.append((rel, i))
    return violations


_EXCEPTION_HEADING = "### Network exceptions, exact path only"


def _security_md_exception_section(security_md):
    """Carve out the one section of SECURITY.md the zero-network test reads
    as its source of truth: the text between _EXCEPTION_HEADING and the next
    heading of the same or higher level (or end of file). Returns "" if the
    heading is missing, which every caller below treats as "nothing is
    documented" rather than crashing on a missing match, so a renamed
    heading shows up as every allowlisted path failing its documentation
    check rather than a traceback that hides which one."""
    i = security_md.find(_EXCEPTION_HEADING)
    if i == -1:
        return ""
    rest = security_md[i + len(_EXCEPTION_HEADING):]
    m = re.search(r"^#{2,3} ", rest, re.M)
    return rest[:m.start()] if m else rest


def _path_is_documented_exception(rel, section):
    """True only when `rel` appears as its own bulleted, backtick-quoted
    line inside the carved-out section above: `- \\`rel\\` ...`. A path that
    merely appears somewhere ELSE in SECURITY.md (an unrelated mention, a
    checksum manifest entry, a changelog line) can never satisfy this: the
    old check was `self.assertIn(rel, security_md)`, a plain substring test
    across the WHOLE document, which `scripts/checksums.sh` already passed
    for a reason that has nothing to do with the network scan (see
    "Verifying what you installed" in SECURITY.md) -- adding it to
    SHELL_NETWORK_ALLOWLIST would have satisfied that old check while being
    documented nowhere as a network exception. This function is what closes
    that hole; test_an_incidental_mention_does_not_satisfy_the_documentation_
    requirement below pins the checksums.sh case by name."""
    return re.search(r"^- `%s`" % re.escape(rel), section, re.M) is not None


class TestAuditableSurface(unittest.TestCase):
    # Four operator scripts, each a network client by design: banning their
    # own network call would defeat the one thing each is for rather than
    # prove anything about the rest of the tree. Every one is invoked only
    # when a person runs it directly (or, for local-gates.sh, from a
    # `workflow_dispatch` CI job someone triggered by hand), never from a
    # gate, a hook, or any default path -- and named, with what it sends and
    # whether it reads or writes, in SECURITY.md's "Network exceptions,
    # exact path only" section.
    #   tools/sbe_bb_estate_check.sh -- read, the Bitbucket estate Pipelines
    #     check (checked with `grep -rln sbe_bb_estate_check` over the
    #     tracked tree, 2026-08-21: the only hits are this file's own
    #     definition and an unrelated handover document, never a pipeline, a
    #     hook or a `bin/sbe` subcommand); zero network attempts before its
    #     keychain credential is confirmed present.
    #   install.sh -- read, `git ls-remote` then `git clone` or `git -C ...
    #     pull --ff-only`; runs once, before any session exists.
    #   scripts/branch-inventory.sh -- read, `git ls-remote --heads origin`.
    #   scripts/local-gates.sh -- read (`git fetch origin main`) and WRITE
    #     (`gh api -X POST` on a GitHub origin; delegates to
    #     src/brothersbe/bbstatus.py, the Python allowlist's own WRITE entry,
    #     on a Bitbucket one).
    # Each is named by exact path so no sibling script can hide behind one.
    # The assertions in test_the_zero_network_property_holds_by_ast below
    # keep this set honest: an entry that stops existing on disk, or one
    # SECURITY.md stops naming as its own structured line (not merely
    # mentioning, see _path_is_documented_exception), fails the test rather
    # than riding along unexamined.
    SHELL_NETWORK_ALLOWLIST = frozenset({
        "tools/sbe_bb_estate_check.sh",
        "install.sh",
        "scripts/branch-inventory.sh",
        "scripts/local-gates.sh",
    })

    def test_the_stated_line_count_tracks_the_tree(self):
        """SECURITY.md states the size of the auditable surface instead of only
        inviting the reader to measure it, because an invitation with no
        baseline is not a claim anyone can check. A stated number nothing
        recomputes goes stale silently; this test recomputes it and fails past
        15 percent drift, so the claim degrades loudly. It does not judge
        whether the surface is small, only that the stated figure is true."""
        body = io.open(os.path.join(HERE, "..", "SECURITY.md")).read()
        m = re.search(r"([\d,]+) lines measured", body)
        self.assertTrue(m, "SECURITY.md no longer states the measured line count")
        said = int(m.group(1).replace(",", ""))
        live = 0
        for p in glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "*.sh")):
            live += sum(1 for _ in io.open(p, errors="replace"))
        drift = abs(live - said) / float(said)
        self.assertLessEqual(drift, 0.15,
                             "tools/ holds %d lines but SECURITY.md says %d (%.0f%% drift); "
                             "re-measure with `wc -l tools/*.py tools/*.sh` and update the "
                             "stated figure" % (live, said, drift * 100))

    def test_the_zero_network_property_holds_by_ast(self):
        """SECURITY.md invites a grep and used to pin its hit count in prose,
        and the pinned number rotted (a count written into prose that nothing
        recomputes). The docs now state the PROPERTY instead, and this test is
        the recomputation the property gets: no tool imports urllib, requests,
        socket or http, and no shell tool invokes curl, wget or nc outside a
        comment. The scan walks the same surface SECURITY.md's own audit grep
        names: tools/, src/brothersbe/, hooks/, scripts/, bin/sbe and
        install.sh, with five allow-listed Python exceptions:
        src/brothersbe/prverify.py, its Bitbucket sibling
        src/brothersbe/bbprverify.py, the write client
        src/brothersbe/bbstatus.py, the read-only `gh api` client
        src/brothersbe/protections.py, and the reserved-but-not-yet-built
        src/brothersbe/gui/server.py, each named and skipped by exact path
        rather than by directory so no other module can hide behind it: it
        is `sbe pr verify`'s own documented GitHub API client (SECURITY.md,
        docs/KNOWN-LIMITS.md lines 713-731). The redaction fixture in this
        file carries curl inside a string on purpose; parsing imports rather
        than grepping text is what keeps that fixture from being a false
        hit.

        CHANGED FOR the codex-b-network-claim review. Three findings against
        the property this test recomputes, all closed here:

        (1) The claim was unfalsifiable: real network operations in
        scripts/branch-inventory.sh (`git ls-remote --heads origin`) and
        scripts/local-gates.sh (`git fetch --quiet origin main`, and
        `gh api -X POST repos/$REPO/statuses/$SHA`, a WRITE) sat in the
        scanned surface, invisible to a scan that looked only for curl, wget
        and nc. install.sh's own `git ls-remote`/`git clone`/`git -C ...
        pull --ff-only` calls were EXPLICITLY exempted by name in this
        docstring's previous revision, not merely unseen. The shell half of
        _shell_network_violations now also looks for a git remote operation
        (ls-remote, fetch, push, clone, pull) and `gh api` as real
        invocations (_shell_network_hit), and install.sh, branch-inventory.sh
        and local-gates.sh all became allow-listed, exact-path, documented
        exceptions rather than blind spots (SHELL_NETWORK_ALLOWLIST above;
        SECURITY.md's Network exceptions section).

        (2) The Python half only ever caught a bare `import`:
        `subprocess.run(["curl", url])` passed clean, and so, discovered
        while measuring how big this fix needed to be, did the tree's own
        real, undocumented network entry point,
        src/brothersbe/protections.py's `argv = ["gh", "api", ...]` followed by a
        bare subprocess run of that argv list, with no check.
        (Written in words rather than as the call itself: this docstring is
        scanned by the silent-failure lint, which is regex over text and
        strips comments but not strings, so an exact copy of the shape here
        FAILS the whole gate battery on a sentence describing the defect.
        Measured 2026-08-21: it blocked the battery at command 3 of 52.)
        _banned_subprocess_violations now AST-walks
        every List/Tuple literal for curl/wget/nc or `gh api`, whether the
        list is written inline or assigned to a variable first, and
        protections.py joined the Python allowlist and SECURITY.md's
        exceptions list in the same change (it was real and shipping before
        this fix; the fix is documenting and covering it, not authorizing
        it new).

        Measured and deliberately NOT widened: a bare `git clone`/`fetch`/
        `pull`/`push`/`ls-remote` Python subprocess call is not banned,
        because three files in this tree's own test suite build exactly
        that shape to clone a LOCAL tempdir fixture (no network reached),
        and flagging them would need either a false allowlist entry or an
        unwarranted heuristic; see _banned_subprocess_violations's docstring
        for the measurement.

        (3) `self.assertIn(rel, security_md)` was a plain substring test
        across the WHOLE document: `scripts/checksums.sh` already appears in
        SECURITY.md for an unrelated reason (the install checksum
        manifest), so adding it to SHELL_NETWORK_ALLOWLIST would have
        satisfied that check while being documented nowhere as a network
        exception. Replaced with _path_is_documented_exception, which
        requires the path to appear as its own line inside SECURITY.md's
        "Network exceptions, exact path only" section, in the shape
        `` - `path` `` -- an incidental mention elsewhere no longer counts.
        Both allowlists (Python and shell) are checked against this same
        structured section, not two different mechanisms.

        The 2026-08-05 amendment (docs/adr/2026-08-05-gui-server-amendment.md,
        gate LP-0301) reserves src/brothersbe/gui/server.py for a
        loopback-only GUI workspace. That path does not exist in this tree
        yet, so this test's result against the real repository is unchanged
        by the reservation (its existence check is skipped for the Python
        allowlist for exactly this reason; the shell allowlist has no such
        reservation and every shell entry must exist).
        TestGuiNetworkAllowlistIsNarrow below proves, against a scratch
        copy, that any OTHER file under src/brothersbe/gui/ still fails this
        scan and that the reserved path itself does not."""
        ROOT = os.path.abspath(os.path.join(HERE, ".."))
        allowlisted = _zero_network_allowlist(ROOT)
        py_files = _zero_network_scan_paths(ROOT, HERE)
        violations = _banned_import_violations(py_files, allowlisted)
        self.assertEqual(
            [], violations,
            "; ".join("%s imports %s" % (os.path.relpath(p, ROOT), ", ".join(hit))
                      for p, hit in violations) +
            " -- the zero-network claim in SECURITY.md is broken")

        subprocess_violations = _banned_subprocess_violations(py_files, allowlisted)
        self.assertEqual(
            [], subprocess_violations,
            "; ".join("%s:%d invokes %s via subprocess"
                      % (os.path.relpath(p, ROOT), lineno, ", ".join(hit))
                      for p, lineno, hit in subprocess_violations) +
            " -- the zero-network claim in SECURITY.md is broken")

        security_md = io.open(os.path.join(ROOT, "SECURITY.md"), encoding="utf-8").read()
        section = _security_md_exception_section(security_md)
        self.assertTrue(section.strip(),
                        "SECURITY.md carries no %r section (or it is empty); "
                        "the zero-network test has nothing to check either "
                        "allowlist's entries against" % _EXCEPTION_HEADING)
        for rel in sorted(self.SHELL_NETWORK_ALLOWLIST):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)),
                            "%s is allowlisted out of the zero-network scan but "
                            "does not exist; the allowlist may only name real "
                            "files" % rel)
            self.assertTrue(_path_is_documented_exception(rel, section),
                            '%s is allowlisted out of the zero-network scan but is '
                            'not listed as its own "- `%s`" line inside SECURITY.md\'s '
                            '%r section; an incidental mention elsewhere in the '
                            'document is not documentation'
                            % (rel, rel, _EXCEPTION_HEADING))
        for p in sorted(allowlisted):
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            self.assertTrue(_path_is_documented_exception(rel, section),
                            '%s is allowlisted out of the zero-network AST scan but '
                            'is not listed as its own "- `%s`" line inside '
                            'SECURITY.md\'s %r section'
                            % (rel, rel, _EXCEPTION_HEADING))

        sh_files = _zero_network_shell_paths(ROOT, HERE)
        sh_violations = _shell_network_violations(ROOT, sh_files, self.SHELL_NETWORK_ALLOWLIST)
        self.assertEqual(
            [], sh_violations,
            "; ".join("%s:%d invokes curl, wget, nc, a git remote operation, "
                      "or gh api" % (rel, i) for rel, i in sh_violations) +
            " -- the zero-network claim in SECURITY.md is broken")


class TestGuiNetworkAllowlistIsNarrow(unittest.TestCase):
    """Regression tests for the 2026-08-05 SECURITY.md amendment
    (docs/adr/2026-08-05-gui-server-amendment.md, gate LP-0301): the zero-
    network scan's new src/brothersbe/gui/server.py allowlist entry must be
    an exact-path exception, not a hole the whole gui/ directory can hide
    behind. Both tests run the PRODUCTION scan helpers
    (_zero_network_scan_paths, _zero_network_allowlist,
    _banned_import_violations, all defined above TestAuditableSurface)
    against a throwaway scratch copy under /tmp rather than the real
    repository, per the house rule that control-weakening calibration
    happens only in a scratch copy, never against the live tree."""

    def test_a_planted_banned_import_in_another_gui_file_is_caught(self):
        with tempfile.TemporaryDirectory() as scratch:
            gui_dir = os.path.join(scratch, "src", "brothersbe", "gui")
            os.makedirs(gui_dir)
            planted = os.path.join(gui_dir, "api.py")
            io.open(planted, "w").write("import socket\n")
            py_files = _zero_network_scan_paths(scratch, scratch)
            allowlisted = _zero_network_allowlist(scratch)
            violations = _banned_import_violations(py_files, allowlisted)
            hit_paths = [p for p, hit in violations]
            self.assertIn(planted, hit_paths,
                          "a planted `import socket` in gui/api.py (not "
                          "gui/server.py) must be caught: the allowlist is an "
                          "exact-path exception, not a directory-wide one")

    def test_the_allowlisted_server_path_itself_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as scratch:
            gui_dir = os.path.join(scratch, "src", "brothersbe", "gui")
            os.makedirs(gui_dir)
            server = os.path.join(gui_dir, "server.py")
            io.open(server, "w").write("import socket\n")
            py_files = _zero_network_scan_paths(scratch, scratch)
            allowlisted = _zero_network_allowlist(scratch)
            violations = _banned_import_violations(py_files, allowlisted)
            hit_paths = [p for p, hit in violations]
            self.assertNotIn(server, hit_paths,
                             "src/brothersbe/gui/server.py is the one named "
                             "exception the 2026-08-05 amendment authorizes; "
                             "it must not be flagged by the scan")


class TestNetworkScanWidenedCoverage(unittest.TestCase):
    """Mutation proofs for the codex-b-network-claim review's three findings
    (SECURITY.md's zero-network claim was blind to git/gh-api, one language
    caught curl and the other did not, and the shell allowlist's
    documentation check was a plain substring match an incidental mention
    could satisfy). Plants and scratch copies here, never the live tree, per
    the house rule that control-weakening calibration happens only in a
    scratch copy (mirrors TestGuiNetworkAllowlistIsNarrow above)."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))

    def test_a_planted_git_fetch_in_a_shell_script_is_caught(self):
        with tempfile.TemporaryDirectory() as scratch:
            os.makedirs(os.path.join(scratch, "scripts"))
            planted = os.path.join(scratch, "scripts", "planted.sh")
            io.open(planted, "w").write("#!/bin/sh\ngit fetch origin main\n")
            sh_files = _zero_network_shell_paths(scratch, scratch)
            violations = _shell_network_violations(scratch, sh_files, frozenset())
            self.assertIn((("scripts/planted.sh", 2)), violations,
                          "a planted `git fetch origin main` must be caught, "
                          "naming the file and line: %r" % (violations,))

    def test_a_planted_gh_api_post_in_a_shell_script_is_caught(self):
        with tempfile.TemporaryDirectory() as scratch:
            os.makedirs(os.path.join(scratch, "scripts"))
            planted = os.path.join(scratch, "scripts", "planted.sh")
            io.open(planted, "w").write(
                '#!/bin/sh\ngh api -X POST "repos/x/statuses/y"\n')
            sh_files = _zero_network_shell_paths(scratch, scratch)
            violations = _shell_network_violations(scratch, sh_files, frozenset())
            self.assertIn(("scripts/planted.sh", 2), violations,
                          "a planted `gh api -X POST ...` must be caught, "
                          "naming the file and line: %r" % (violations,))

    def test_a_dry_run_message_describing_git_clone_is_not_mistaken_for_running_it(self):
        """Calibrated in BOTH directions, because a blanker that suppresses
        everything would pass the first half for the wrong reason: the
        PROSE line (a dry-run message that PRINTS the words, exactly the
        install.sh shape that motivated _shell_code_only) must not be
        flagged, and a REAL invocation two lines later in the SAME file
        must still be, naming its own line, not the prose line's."""
        with tempfile.TemporaryDirectory() as scratch:
            os.makedirs(os.path.join(scratch, "scripts"))
            planted = os.path.join(scratch, "scripts", "planted.sh")
            io.open(planted, "w").write(
                '#!/bin/sh\n'
                'echo "would run: git clone $origin_url $clone_dest"\n'
                'git clone "$origin_url" "$clone_dest"\n')
            sh_files = _zero_network_shell_paths(scratch, scratch)
            violations = _shell_network_violations(scratch, sh_files, frozenset())
            self.assertEqual([("scripts/planted.sh", 3)], violations,
                             "the dry-run message on line 2 must not be flagged "
                             "and the real invocation on line 3 must be, naming "
                             "line 3 exactly: %r" % (violations,))

    def test_a_planted_curl_via_python_subprocess_is_caught(self):
        """The exact reproduction from the review: `subprocess.run(["curl",
        url])` used to pass tools/test_sbe.py TestAuditableSurface clean."""
        with tempfile.TemporaryDirectory() as scratch:
            planted = os.path.join(scratch, "planted.py")
            io.open(planted, "w").write(
                'import subprocess\n'
                'subprocess.run(["curl", "https://example.invalid"])\n')
            py_files = _zero_network_scan_paths(scratch, scratch)
            violations = _banned_subprocess_violations(py_files, set())
            # Not a `["curl"]` list literal in this assertion on purpose:
            # this file is itself inside the surface being scanned, and that
            # exact literal is what the scan looks for (see _PY_NETWORK_TOKENS
            # above), so writing it here would flag this test file.
            hits = [(os.path.relpath(p, scratch), lineno, hit)
                    for p, lineno, hit in violations if lineno == 2]
            self.assertTrue(hits, "a planted subprocess curl call must be "
                            "caught, naming its file and line: %r" % (violations,))
            self.assertEqual(hits[0][0], "planted.py")
            self.assertIn("curl", hits[0][2])

    def test_a_planted_gh_api_built_into_a_variable_first_is_caught(self):
        """The shape src/brothersbe/protections.py's real `gh api` call
        uses: the argument vector is assigned to a local name before
        subprocess.run reads it, not written inline. A scan that only looked
        at Call-site literals would miss this exactly as it missed the real
        file, which is what made protections.py a real, undocumented network
        entry point until this change."""
        with tempfile.TemporaryDirectory() as scratch:
            os.makedirs(os.path.join(scratch, "src", "brothersbe"))
            planted = os.path.join(scratch, "src", "brothersbe", "planted.py")
            io.open(planted, "w").write(
                'import subprocess\n'
                'def f(path):\n'
                '    argv = ["gh", "api", "-H", "Accept: x", path]\n'
                '    subprocess.run(argv)\n')
            py_files = _zero_network_scan_paths(scratch, scratch)
            violations = _banned_subprocess_violations(py_files, set())
            hits = [(os.path.relpath(p, scratch).replace(os.sep, "/"), lineno, hit)
                    for p, lineno, hit in violations]
            self.assertIn(("src/brothersbe/planted.py", 3, ["gh api"]), hits,
                          "a `gh api` argv built into a variable first must be "
                          "caught: %r" % (hits,))

    def test_an_incidental_mention_does_not_satisfy_the_documentation_requirement(self):
        """Pins the exact finding-3 case against the REAL SECURITY.md:
        `scripts/checksums.sh` genuinely appears in the document already
        (the install checksum manifest section), so the OLD check
        (`assertIn(rel, security_md)`, a plain substring test) would have
        silently passed it if it were ever added to SHELL_NETWORK_ALLOWLIST
        without also being documented as a network exception. The new check
        must reject it: no `- \\`scripts/checksums.sh\\`` line exists inside
        the Network exceptions section, because checksums.sh is not a
        network exception at all."""
        security_md = io.open(os.path.join(self.ROOT, "SECURITY.md"),
                              encoding="utf-8").read()
        self.assertIn("scripts/checksums.sh", security_md,
                      "this proof needs the real incidental mention to still "
                      "exist (the install checksum manifest section); if this "
                      "fails, re-target the proof at whatever path SECURITY.md "
                      "now mentions incidentally")
        section = _security_md_exception_section(security_md)
        self.assertFalse(
            _path_is_documented_exception("scripts/checksums.sh", section),
            "scripts/checksums.sh must NOT read as a documented network "
            "exception: it is mentioned in SECURITY.md for an unrelated "
            "reason, and the old assertIn-based check could not tell the "
            "difference")

    def test_removing_a_genuine_allowlist_entry_exposes_its_real_offender(self):
        """tools/sbe_bb_estate_check.sh carries a real, on-purpose `curl`
        call (SECURITY.md, "Network exceptions"). Drop it from the shell
        allowlist without touching the file on disk and the scan must name
        it, proving the allowlist is load-bearing rather than decorative."""
        HERE_REAL = os.path.join(self.ROOT, "tools")
        sh_files = _zero_network_shell_paths(self.ROOT, HERE_REAL)
        shrunk = TestAuditableSurface.SHELL_NETWORK_ALLOWLIST - {
            "tools/sbe_bb_estate_check.sh"}
        violations = _shell_network_violations(self.ROOT, sh_files, shrunk)
        offenders = [rel for rel, _i in violations]
        self.assertIn("tools/sbe_bb_estate_check.sh", offenders,
                      "removing the one real, deliberate curl caller from the "
                      "allowlist must expose it as a violation, naming it by "
                      "path: %r" % (violations,))


class TestAutosaveRecover(unittest.TestCase):
    def test_recover_writes_nothing_into_the_source_worktree(self):
        """recover must check the snapshot out into a NEW worktree, never into
        the live one. The old mode printed an in-place `git restore` that could
        delete a tracked file the snapshot never captured; this test pins the
        replacement: source tree byte-identical before and after, no in-place
        restore command in the output, snapshot content present in the new
        worktree. It does not test permissions enforcement (platform-dependent,
        reported by the tool rather than promised)."""
        hook = os.path.join(HERE, "sbe_autosave.py")
        with tempfile.TemporaryDirectory() as repo:
            def git(*a):
                return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
            io.open(os.path.join(repo, "tracked.txt"), "w").write("v1")
            git("add", "-A"); git("commit", "-qm", "init")
            io.open(os.path.join(repo, "wip.txt"), "w").write("UNLANDED")
            vdir = tempfile.mkdtemp()
            env = dict(os.environ, BROTHERSBE_VAULT=vdir)
            subprocess.run([sys.executable, hook, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent test harness fires the hook; recover output is asserted below
                           text=True, env=env)
            # Simulate the loss the autosave exists for: the WIP file is gone.
            os.remove(os.path.join(repo, "wip.txt"))
            before_status = git("status", "--porcelain").stdout
            before_files = sorted(os.listdir(repo))
            r = subprocess.run([sys.executable, hook, "recover", repo], capture_output=True,
                               text=True, env=env)
            # Source worktree byte-identical: recover wrote nothing here.
            self.assertEqual(before_status, git("status", "--porcelain").stdout)
            self.assertEqual(before_files, sorted(os.listdir(repo)))
            # The data-loss path must be gone from the output, not merely warned about.
            self.assertNotIn("--worktree .", r.stdout, "in-place restore path resurfaced")
            self.assertIn("never touched", r.stdout)
            # The new worktree exists and contains the lost work.
            lines = [l.strip() for l in r.stdout.splitlines()]
            wt = next((l for l in lines if os.path.isdir(l)), "")
            self.assertTrue(wt, "recover did not print a recovery worktree path:\n%s" % r.stdout)
            body = io.open(os.path.join(wt, "wip.txt")).read()
            self.assertEqual(body, "UNLANDED")
            git("worktree", "remove", "--force", wt)


class TestHandoff(unittest.TestCase):
    def test_handoff_redacts_and_preserves(self):
        with tempfile.TemporaryDirectory() as v:
            base = os.path.join(v, "10-Projects", "demo", "Sessions")
            os.makedirs(base)
            proj = os.path.dirname(base)
            io.open(os.path.join(proj, "Overview.md"), "w").write(
                "builds X. the prod password is hunter2")
            io.open(os.path.join(base, "s.md"), "w").write("used DB_PASSWORD=s3cr3t here")
            env = dict(os.environ, BROTHERSBE_VAULT=v)
            with tempfile.TemporaryDirectory() as cwd:
                r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_telemetry.py"),
                                    "handoff", "demo"], env=env, cwd=cwd,
                                   capture_output=True, text=True)
                out = os.path.join(cwd, "handoff-demo.md")
                self.assertTrue(os.path.exists(out), "handoff file not written")
                body = io.open(out).read()
                self.assertNotIn("hunter2", body)
                self.assertNotIn("s3cr3t", body)
                self.assertIn("builds X", body)
                self.assertIn("[REDACTED]", body)


class TestLintSelfSkipThroughSymlink(unittest.TestCase):
    def test_a_symlinked_lint_root_is_the_same_tree(self):
        """The lint excludes its own source by PATH, and abspath does not
        resolve symlinks, so the same tree reached through a symlinked
        spelling (macOS /tmp vs /private/tmp, a bind mount) made the tool scan
        itself: the unwaivable gate FAILed an honest tree, naming four
        "defects" that were its own regex literals, and the self-skip
        disclosure vanished. Both spellings must produce the same verdict and
        both must carry the self-skip disclosure."""
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "linked-tools")
            os.symlink(HERE, link)
            outputs = []
            for root in (HERE, link):
                r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                                   env=dict(os.environ, SBE_LINT_ROOT=root,
                                            BROTHERSBE_REGISTRIES=""),
                                   capture_output=True, text=True)
                line = next((l for l in r.stdout.splitlines()
                             if l.startswith("silent-failure-lints")), "")
                self.assertIn("own source was not scanned", line,
                              "self-skip disclosure missing for root %s: %s" % (root, line))
                outputs.append(line.split()[1])   # the verdict token
            self.assertEqual(outputs[0], outputs[1],
                             "one tree, two verdicts, depending on path spelling: %r" % outputs)

    def test_a_language_scoped_pattern_never_fires_on_another_language(self):
        """`try!` is Swift syntax that cannot occur in Python, and the pattern
        for it used to run against every scannable extension. It therefore
        matched the ENGLISH WORD in ordinary prose: a pure-Python file whose
        docstring reads "Give it a try!" was reported as a discarded error at
        gate severity, which under --strict blocks a merge. Found 2026-07-31 by
        running this lint against pallets/click, whose examples/colors/colors.py
        carries exactly that sentence.

        Calibrated in BOTH directions on purpose, because a test written only
        from the fix would pass just as well if the pattern had been deleted:
        the prose file must come back clean AND a real Swift `try!` must still
        FAIL. Deleting the pattern satisfies the first assertion and breaks the
        second."""
        def lint_line(root):
            r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                               env=dict(os.environ, SBE_LINT_ROOT=root,
                                        BROTHERSBE_REGISTRIES=""),
                               capture_output=True, text=True)
            return next((l for l in r.stdout.splitlines()
                         if l.startswith("silent-failure-lints")), "")

        with tempfile.TemporaryDirectory() as d:
            prose = os.path.join(d, "prose")
            os.makedirs(prose)
            io.open(os.path.join(prose, "harmless.py"), "w").write(
                '"""A module with no error handling at all.\n\n'
                'Give it a try! This sentence is prose in a docstring.\n"""\n\n\n'
                "def add(a, b):\n    return a + b\n")
            line = lint_line(prose)
            self.assertIn("PASS", line.split()[:2],
                          "the English word in a docstring was read as Swift "
                          "syntax: %s" % line)
            self.assertNotIn("force-try", line, line)

            swift = os.path.join(d, "swift")
            os.makedirs(swift)
            io.open(os.path.join(swift, "Real.swift"), "w").write(
                "func load() {\n    try! risky()\n}\n")
            line = lint_line(swift)
            self.assertIn("FAIL", line.split()[:2],
                          "a real Swift force-try stopped being caught, so the "
                          "scope removed the rule instead of aiming it: %s" % line)
            self.assertIn("force-try", line, line)

    def test_a_pattern_shaped_full_line_comment_is_not_a_hit(self):
        """A doc comment that names a swallow pattern in prose, the way this
        very file's LINT_PATTERNS section does, must not be mistaken for
        committing it. A match only counts once at least one line it spans
        is not a full-line comment."""
        def lint_line(root):
            r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                               env=dict(os.environ, SBE_LINT_ROOT=root,
                                        BROTHERSBE_REGISTRIES=""),
                               capture_output=True, text=True)
            return next((l for l in r.stdout.splitlines()
                         if l.startswith("silent-failure-lints")), "")

        with tempfile.TemporaryDirectory() as d:
            io.open(os.path.join(d, "documented.py"), "w").write(
                "def safe():\n    return 1\n\n\n"
                "# Do not write a bare exc" + "ept: here, it hides the real error.\n")
            line = lint_line(d)
            self.assertIn("PASS", line.split()[:2],
                          "pattern-shaped text inside a full-line comment was "
                          "read as a real swallow: %s" % line)
            self.assertNotIn("bare except", line, line)

    def test_the_same_pattern_shaped_text_on_a_code_line_is_still_a_hit(self):
        """The comment-aware check above must narrow the lint, never blind
        it: the identical words, written as real code instead of a comment,
        still have to FAIL."""
        def lint_line(root):
            r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                               env=dict(os.environ, SBE_LINT_ROOT=root,
                                        BROTHERSBE_REGISTRIES=""),
                               capture_output=True, text=True)
            return next((l for l in r.stdout.splitlines()
                         if l.startswith("silent-failure-lints")), "")

        with tempfile.TemporaryDirectory() as d:
            io.open(os.path.join(d, "risky.py"), "w").write(
                "def risky():\n    try:\n        return 1 / 0\n"
                "    exc" + "ept:\n        return None\n")
            line = lint_line(d)
            self.assertIn("FAIL", line.split()[:2],
                          "a real bare except stopped being caught once "
                          "comment-shaped matches were excluded: %s" % line)
            self.assertIn("bare except", line, line)

    @unittest.skipIf(os.name != "posix", "needs a hardlink")
    def test_a_directory_mostly_self_skipped_names_the_count_and_withdraws_clean(self):
        """review-13a: a directory where the walk reaches its own source under
        thirteen of its fourteen names (a hardlink or a case/symlink alias
        reaches the same inode more than once) printed "1 file(s) scanned
        under X, clean" with the thirteen self-skipped files listed by NAME
        and no number attached, and the "clean in what was opened, which is
        not the same as a clean tree" withdrawal only checked unread_total
        (the KIND-skip reason), never self_skipped, so the bare word "clean"
        stood over a directory that was thirteen fourteenths unexamined.
        Reproduced with hardlinks, which share sbe_score.py's own inode
        without needing a second copy on disk."""
        with tempfile.TemporaryDirectory() as d:
            n_links = 13
            for i in range(n_links):
                os.link(os.path.join(HERE, "sbe_score.py"),
                        os.path.join(d, "copy%d.py" % i))
            io.open(os.path.join(d, "clean.py"), "w").write("def f():\n    return 1\n")
            r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                               env=dict(os.environ, SBE_LINT_ROOT=d, BROTHERSBE_REGISTRIES=""),
                               capture_output=True, text=True)
            line = next((l for l in r.stdout.splitlines()
                         if l.startswith("silent-failure-lints")), "")
            self.assertIn("PASS", line.split()[:2], "unexpected verdict: %s" % line)
            self.assertIn("%d file(s)" % n_links, line,
                          "the self-skipped files are named but never counted: %s" % line)
            self.assertIn("clean in what was opened, which is not the same as a clean tree", line,
                          "self-skip alone left the bare word clean standing over a directory "
                          "that was mostly unexamined: %s" % line)
            # The control: a directory with nothing self-skipped keeps the
            # plain "N file(s) scanned under X, clean" sentence, so this is a
            # disclosure rule and not a new withdrawal fired unconditionally.
            with tempfile.TemporaryDirectory() as clean_d:
                io.open(os.path.join(clean_d, "clean.py"), "w").write("def f():\n    return 1\n")
                r2 = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py")],
                                    env=dict(os.environ, SBE_LINT_ROOT=clean_d,
                                             BROTHERSBE_REGISTRIES=""),
                                    capture_output=True, text=True)
                line2 = next((l for l in r2.stdout.splitlines()
                             if l.startswith("silent-failure-lints")), "")
                self.assertIn(", clean", line2)
                self.assertNotIn("not the same as a clean tree", line2,
                                 "a tree with nothing skipped lost its plain clean sentence: %s"
                                 % line2)


class TestOneLineNeutralizesTheControlClass(unittest.TestCase):
    def test_no_control_or_format_character_survives_into_a_report_line(self):
        """one_line() used to flatten line-break characters only, and a
        terminal defines a line by the cursor, not by newlines: ESC [ 1F
        rewrote the rendered line above and ESC E opened a new one, so a
        receipt field forged whole verdict lines while the byte stream held
        one line per gate. The rule is the class: every Cc and Cf code point
        arrives as a visible escape, so the probe below asserts over members
        no list in the fix names (backspace, ESC E, a bidi override) as well
        as the ones the review demonstrated."""
        spec2 = importlib.util.spec_from_file_location(
            "sbe_checks_ol", os.path.join(HERE, "sbe_checks.py"))
        checks = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(checks)
        import unicodedata
        probes = ["a\x1b[2K\x1b[1Fforged PASS", "x\x1bEy", "a\x08b",
                  "rtl‮forged", "nel\x85nel", "del\x7fdel", "bom﻿bom"]
        for p in probes:
            out = checks.one_line(p)
            leaked = [ch for ch in out
                      if unicodedata.category(ch) in ("Cc", "Cf", "Cs")]
            self.assertEqual(leaked, [],
                             "control/format characters survived one_line(%r): %r" % (p, out))
        # The escape is visible, not a deletion: the reader must be able to
        # see and question what the artifact tried to do.
        self.assertIn("\\x1b", checks.one_line("x\x1bEy"))
        self.assertIn("\\u202e", checks.one_line("rtl‮forged"))


class TestNestedCheckoutPrune(unittest.TestCase):
    """A directory carrying its own .git entry is another repository's surface.

    The real event: a concurrent session's linked worktree under
    .claude/worktrees/ put a STALE COPY of sbe_score.py through the unwaivable
    lint gate, which then failed this repository for defects that exist only
    in the copy. The prune must skip the nested checkout AND say so, and the
    defects inside it must still be findable when the nested tree itself is
    the scan root, or the prune would be a blindfold rather than a boundary."""

    def test_a_nested_checkout_is_pruned_by_name_and_scanned_when_targeted(self):
        import subprocess as sp
        d = tempfile.mkdtemp()
        try:
            io.open(os.path.join(d, "clean.py"), "w").write("def f():\n    return 1\n")
            nested = os.path.join(d, "wt")
            os.makedirs(nested)
            io.open(os.path.join(nested, ".git"), "w").write("gitdir: /elsewhere\n")
            io.open(os.path.join(nested, "bad.py"), "w").write(
                "def g():\n    try:\n        x = 1\n    exc" + "ept:\n        pass\n")
            out = sp.run([sys.executable, os.path.join(HERE, "sbe_score.py"), d],
                         capture_output=True, text=True)
            line = [l for l in out.stdout.splitlines()
                    if l.startswith("silent-failure-lints")][0]
            self.assertIn("PASS", line.split()[1],
                          "the root scan must not be failed by the nested checkout: %s" % line)
            self.assertIn("nested git checkout", out.stdout,
                          "the prune must be named, never silent")
            out2 = sp.run([sys.executable, os.path.join(HERE, "sbe_score.py"), nested],
                          capture_output=True, text=True)
            line2 = [l for l in out2.stdout.splitlines()
                     if l.startswith("silent-failure-lints")][0]
            self.assertIn("FAIL", line2,
                          "scanning the nested tree directly must still find its "
                          "defect; the prune is a boundary, not a blindfold: %s" % line2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestStrictMode(unittest.TestCase):
    def test_severity_decides_what_a_strict_run_blocks_on(self):
        """The severity each check declares at write time is what a FAIL does to
        the exit code, and nothing else: advisory runs exit 0 whatever they
        find, --strict blocks on gate severity, and soft severity blocks only
        under the opt-in --strict-soft. A vault with an active session but no
        session log forces a soft FAIL (vault-log-per-active-day); a bare
        except with no waiver forces a gate FAIL (silent-failure-lints)."""
        import datetime
        with tempfile.TemporaryDirectory() as v:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            io.open(os.path.join(teld, "outcomes.jsonl"), "w").write(
                json.dumps({"schema": 2, "ts": now, "session_id": "x", "project": "p",
                            "tool_calls": 5, "api_msgs": 9}) + "\n")
            env = dict(os.environ, BROTHERSBE_VAULT=v, SBE_LINT_ROOT="")
            score = os.path.join(HERE, "sbe_score.py")
            advisory = subprocess.run([sys.executable, score], env=env,
                                      capture_output=True, text=True)
            strict = subprocess.run([sys.executable, score, "--strict"], env=env,
                                    capture_output=True, text=True)
            strict_soft = subprocess.run([sys.executable, score, "--strict", "--strict-soft"],
                                         env=env, capture_output=True, text=True)
            self.assertEqual(advisory.returncode, 0, "advisory mode must never block (exit 0)")
            self.assertEqual(strict.returncode, 0,
                             "--strict must not block on a soft-severity FAIL alone")
            self.assertIn("soft-severity", strict.stdout,
                          "--strict must NAME the soft FAILs it declined to block on")
            self.assertEqual(strict_soft.returncode, 1,
                             "--strict-soft must block on a soft-severity FAIL")
            # A gate-severity FAIL blocks under plain --strict: point the lint at
            # a tree holding an unwaived bare except.
            lintdir = os.path.join(v, "src")
            os.makedirs(lintdir)
            io.open(os.path.join(lintdir, "evil.py"), "w").write(
                "try:\n    f()\nexcept Exception:\n    pass\n")
            env2 = dict(env, SBE_LINT_ROOT=lintdir)
            strict_gate = subprocess.run([sys.executable, score, "--strict"], env=env2,
                                         capture_output=True, text=True)
            self.assertEqual(strict_gate.returncode, 1,
                             "--strict must exit nonzero on a gate-severity FAIL")
            self.assertIn("[severity: gate]", strict_gate.stdout,
                          "the verdict line must print the severity it declared")


class TestCliSurface(unittest.TestCase):
    """`bin/sbe` is a facade: every built subcommand delegates to a tool in
    tools/ that already carries the behavior and the tests. The failure modes a
    facade adds are its own, and they are what this class pins: a command that
    exists in the code and not in --help (or the reverse), an unbuilt command
    that quietly succeeds at nothing, and an exit code that drifts from what the
    docstring promises a CI job can rely on."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def _run(self, *argv):
        return subprocess.run([sys.executable, self.SBE] + list(argv),
                              capture_output=True, text=True, cwd=ROOT)

    def _commands(self):
        spec = importlib.util.spec_from_file_location(
            "brothersbe_cli", os.path.join(ROOT, "src", "brothersbe", "cli.py"),
            submodule_search_locations=[os.path.join(ROOT, "src", "brothersbe")])
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            import brothersbe.cli as cli
            return cli
        finally:
            sys.path.pop(0)
            del spec

    def test_the_launcher_runs_and_reports_the_one_version_this_project_has(self):
        out = self._run("--version")
        self.assertEqual(out.returncode, 0, out.stderr)
        declared = io.open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
        self.assertIn(declared, out.stdout,
                      "the CLI prints a version that is not the one in VERSION")

    def test_every_advertised_command_has_something_behind_it(self):
        """Scoped honestly, because the first version of this test was close to
        tautological: --help is generated from the same list the dispatcher
        reads, so comparing one to the other proves little. What it pins now is
        the pair of failures that list cannot prevent by construction: a name
        advertised in the help text with no callable behind it, and a command
        whose help line is missing so a reader cannot discover it. Both are
        reachable by hand-editing the epilog or the table."""
        cli = self._commands()
        out = self._run("--help")
        self.assertEqual(out.returncode, 0, out.stderr)
        advertised = set(re.findall(r"^  ([a-z][a-z-]+) {2,}", out.stdout, re.M))
        implemented = set(name for (name, _h, _r) in cli.COMMANDS)
        self.assertEqual(advertised - implemented, set(),
                         "advertised in --help with nothing behind it: %s"
                         % (advertised - implemented))
        self.assertEqual(implemented - advertised, set(),
                         "implemented but undiscoverable in --help: %s"
                         % (implemented - advertised))
        for name, help_, run in cli.COMMANDS:
            self.assertTrue(callable(run), "%s has no runner" % name)
            self.assertTrue(help_.strip(), "%s has no help line" % name)

    def test_an_unbuilt_command_refuses_loudly_and_names_its_wave(self):
        cli = self._commands()
        # inspect-change left this list when `sbe impact` shipped, and adopt left
        # it when wave 9 built the adoption kit: each is now a real command. The
        # list is the wave-by-wave record of what is still owed, so a name leaves
        # it only when something stands behind it.
        # `policy` left this list when the required-evidence engine shipped: it
        # now reads .sbe/policy.yml, evaluates a diff against it and exits 1 on
        # a MISSING requirement. The assertion below pins the direction, so a
        # regression that reduced it back to a refusal fails here rather than
        # passing as "still unbuilt, as declared".
        unbuilt = ["exceptions"]
        built_since = ["policy"]
        known = [n for (n, _h, _r) in cli.COMMANDS]
        for name in built_since:
            self.assertIn(name, known, "%s vanished from the command table" % name)
            # A REAL invocation, not `--help`. Asking argparse for help exits 0
            # before any runner is reached, so a `--help` probe here passed just
            # as happily against the NOT BUILT stub and asserted nothing at all.
            # `--base HEAD` is an empty range, which is the cheapest run that
            # still goes all the way through the command.
            out = self._run(name, "evaluate", ".", "--base", "HEAD", "--json")
            self.assertNotEqual(out.returncode, cli.EXIT_NOT_BUILT,
                                "%s exited %d (NOT BUILT) after it shipped; a built command "
                                "that refuses is worse than one that never existed"
                                % (name, out.returncode))
            self.assertNotIn("NOT BUILT", out.stderr + out.stdout)
            self.assertIn("\"verdict\"", out.stdout,
                          "%s produced no report: %s%s" % (name, out.stdout, out.stderr))
        for name in unbuilt:
            self.assertIn(name, known, "%s vanished from the command table" % name)
            out = self._run(name)
            self.assertEqual(out.returncode, cli.EXIT_NOT_BUILT,
                             "%s exited %d; an unbuilt command must exit %d rather than "
                             "printing an empty result and succeeding"
                             % (name, out.returncode, cli.EXIT_NOT_BUILT))
            self.assertIn("NOT BUILT", out.stderr + out.stdout)
            self.assertRegex(out.stderr + out.stdout, r"wave \d",
                             "%s refuses without saying what will build it" % name)

    def test_the_exit_codes_a_ci_job_would_branch_on(self):
        import re as _re
        cli = self._commands()
        doctor = self._run("doctor")
        if doctor.returncode != cli.EXIT_OK:
            # A clone nobody has run `sbe init` in has no .brothersbe/
            # config.json, so doctor reports project-init SETUP and exits
            # EXIT_CONTROL_FAILED. That is a TRUE report about that clone, and
            # the public export deliberately does not ship the maintainer's
            # own setup marker: shipping it would tell a stranger their fresh
            # clone is already set up when it is not, which is exactly the
            # false green this product exists to refuse. So SETUP with no FAIL
            # is accepted here and named; every other nonzero exit is still a
            # failure, and a run that FAILED a check is never waved through.
            body = doctor.stdout + doctor.stderr
            self.assertEqual(
                doctor.returncode, cli.EXIT_CONTROL_FAILED,
                "doctor exited %d, which is neither EXIT_OK nor the "
                "EXIT_CONTROL_FAILED a not-yet-set-up clone reports: %s"
                % (doctor.returncode, body))
            self.assertIn(
                "project-init", body,
                "doctor exited nonzero without reporting project-init, so the "
                "nonzero exit is not the not-yet-set-up case this branch "
                "accepts: %s" % body)
            self.assertRegex(
                body, r"\b0 FAIL\b",
                "doctor exited nonzero with at least one FAIL, which is a real "
                "defect and must never be accepted as a set-up gap: %s" % body)
        self.assertEqual(self._run("bogus-command").returncode, cli.EXIT_USAGE,
                         "an unknown command must be a usage error, never a silent success")
        self.assertEqual(self._run("verify", "/nonexistent-path-on-purpose").returncode,
                         cli.EXIT_USAGE,
                         "a mistyped path must be a usage error; it must never read as a "
                         "clean scan")

    def test_verify_never_lets_exit_zero_read_as_a_pass(self):
        """The aggregating commands exit 0 when nothing FAILED, and a run where
        every check reported NO-DATA also exits 0. The closing line has to say
        so, because an exit code cannot.

        CHANGED FOR CR-08. This test used to also be the one place that pinned
        `sbe verify` minting NOTHING: before this stage, a passing run over an
        empty directory left `.sbe/evidence` unwritten, which is the exact
        defect CR-08 closes (`design/lifecycle-blockers/03-adr.md`) --
        `sbe verify` exits 0 and `sbe status` then reports "no evidence store
        found" about the very run that just happened. `_cmd_verify` now mints
        one receipt per delegate (design, gate, score) into `.sbe/evidence`
        every time it runs, including here: `v` is not even a git repository,
        and the mint step still writes three receipts about it (each reading
        whatever `evidence.generate` can honestly observe there, never
        crashing over the missing repo). So this test now also asserts the
        receipts are present, not just that exit 0 keeps meaning "no control
        FAILED" rather than "a control passed"; the honest exit-0 caveat is
        unchanged and still has to be true at the same time evidence exists.
        """
        v = tempfile.mkdtemp()
        try:
            out = self._run("verify", v)
            self.assertEqual(out.returncode, 0)
            self.assertIn("does not mean a control passed", out.stdout,
                          "verify exited 0 over an empty directory without saying that no "
                          "control passed")
            evidence_dir = os.path.join(v, ".sbe", "evidence")
            self.assertTrue(os.path.isdir(evidence_dir),
                            "sbe verify exited 0 but minted no evidence store at all: %s"
                            % out.stdout)
            minted = set(os.listdir(evidence_dir))
            self.assertEqual(minted, {"design.json", "gate.json", "score.json"},
                             "sbe verify must mint one receipt per delegate it runs "
                             "(design, gate, score), got %r" % minted)
        finally:
            shutil.rmtree(v, ignore_errors=True)

    def test_the_package_imports_with_nothing_installed(self):
        """Zero dependencies is a promise this project makes on its front page.
        A facade that needs a pip install to reach the command line would retract
        it for anyone on a locked-down machine or in a CI image with no index."""
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import brothersbe; "
             "print(brothersbe.__version__)" % os.path.join(ROOT, "src")],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.strip(), "the package imported but reports no version")


class TestVerifyMintsEvidence(unittest.TestCase):
    """CR-08 (`design/lifecycle-blockers/03-adr.md`): `sbe verify` used to
    exit 0 and mint nothing, so `sbe status` on the same directory kept
    reporting "no evidence store found" about a run that just happened.
    `_cmd_verify` now mints one receipt per delegate (design, gate, score)
    into `.sbe/evidence` every time it runs. Every fixture here is a real
    git repository and a real `sbe` subprocess, for the same reason
    `test_sbe_evidence.py` and `test_sbe_status.py` give: the defect lives at
    the seam between what one command wrote and what another reads, and a
    mocked filesystem would test the mock.
    """

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self._git("init", "-q")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "fixture")
        # Named off "main"/"master" on purpose: `evidence.generate`'s default
        # (no --base) diffs against the merge base with the default branch,
        # tried under exactly those two names first, and a repo whose only
        # branch IS named one of them makes that merge-base trivially equal
        # to HEAD itself, so the diff comes back empty. "trunk" keeps the
        # default-coverage receipts below actually covering something.
        self._git("branch", "-m", "trunk")

    def _git(self, *args):
        out = subprocess.run(["git"] + list(args), cwd=self.repo, capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError("git %s failed in %s: %s"
                                 % (" ".join(args), self.repo, out.stderr))
        return out.stdout.strip()

    def _write(self, rel, body):
        path = os.path.join(self.repo, rel)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def _commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD")

    def _sbe(self, *argv):
        return subprocess.run([sys.executable, self.SBE] + list(argv),
                              capture_output=True, text=True)

    def _evidence_dir(self):
        return os.path.join(self.repo, ".sbe", "evidence")

    def _receipt(self, kind):
        with io.open(os.path.join(self._evidence_dir(), "%s.json" % kind),
                     encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_clean_committed_repo_gets_all_three_receipts_and_status_counts_them(self):
        """Acceptance 1: a clean, committed scratch repo. `sbe verify`
        mints design, gate and score receipts at the same commit, and
        `sbe status` stops saying "no evidence store found" and counts
        them."""
        self._write("README.md", "base\n")
        self._commit("base")
        self._write("app.py", "print('hi')\n")
        head = self._commit("add app")

        out = self._sbe("verify", self.repo)
        self.assertEqual(out.stdout.count("does not mean a control passed")
                         + out.stdout.count("at least one control FAILED"), 1, out.stdout)

        minted = set(os.listdir(self._evidence_dir()))
        self.assertEqual(minted, {"design.json", "gate.json", "score.json"}, minted)
        for kind in ("design", "gate", "score"):
            receipt = self._receipt(kind)
            self.assertEqual(receipt["checkKinds"], [kind])
            self.assertEqual(receipt["headCommit"], head,
                             "%s receipt was not minted at the same commit as HEAD" % kind)

        status = self._sbe("status", self.repo, "--json")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertNotIn("no evidence store found", status.stdout + status.stderr,
                         "status still reports no evidence store after verify minted three")
        data = json.loads(status.stdout)
        self.assertEqual(data["scope"]["storesInspected"]["evidenceDir"], self._evidence_dir())
        sound_paths = set(item["path"] for item in data["soundEvidence"])
        self.assertEqual(sound_paths,
                         {".sbe/evidence/design.json", ".sbe/evidence/gate.json",
                          ".sbe/evidence/score.json"},
                         "status did not count all three freshly minted receipts as sound: %r"
                         % data["soundEvidence"])

    def test_a_dirty_tree_still_mints_receipts_but_they_read_no_data_naming_the_dirty_state(self):
        """Acceptance 2: an uncommitted edit sits in the tree. Verify still
        mints; the receipts read NO-DATA naming the dirty state (the ADR's
        law, not a bug); status reports that honestly; exit codes are
        unaffected by any of it."""
        self._write("README.md", "base\n")
        self._commit("base")
        self._write("app.py", "print('hi')\n")
        self._commit("add app")
        clean_out = self._sbe("verify", "/nonexistent-path-on-purpose")
        self.assertEqual(clean_out.returncode, 2, "usage error must still be 2, unaffected")

        self._write("app.py", "print('hi')\nprint('uncommitted edit')\n")
        self.assertTrue(self._git("status", "--porcelain"), "setup check: repo must be dirty")

        out = self._sbe("verify", self.repo)
        exit_over_dirty = out.returncode

        minted = set(os.listdir(self._evidence_dir()))
        self.assertEqual(minted, {"design.json", "gate.json", "score.json"}, minted)
        for kind in ("design", "gate", "score"):
            receipt = self._receipt(kind)
            self.assertIs(receipt["workingTreeDirty"], True,
                          "%s receipt did not name the dirty tree it was minted against" % kind)
            verify_out = self._sbe("evidence", "verify",
                                   os.path.join(self._evidence_dir(), "%s.json" % kind),
                                   "--cwd", self.repo, "--json")
            verdict = json.loads(verify_out.stdout)
            self.assertEqual(verdict["verdict"], "NO-DATA",
                             "%s must read NO-DATA on a dirty tree, never PASS: %r"
                             % (kind, verdict))

        status = self._sbe("status", self.repo, "--json")
        data = json.loads(status.stdout)
        self.assertEqual(data["soundEvidence"], [],
                         "a dirty-tree receipt must never be counted as sound evidence")
        self.assertEqual(data["brokenClaims"], [],
                         "a dirty-tree receipt is NO-DATA, not a broken claim")
        # READ THE FIELD, DO NOT GREP THE SERIALIZED TEXT. This assertion has
        # now been wrong twice for the same underlying reason, so it is worth
        # stating. First it searched the raw stdout for ".sbe/evidence", which
        # passed on POSIX and failed on Windows where the absolute path is
        # spelled with backslashes. Then it searched for the platform spelling,
        # which ALSO failed on Windows, because stdout here is JSON and JSON
        # escapes every backslash: the text really contains ".sbe\\evidence".
        # Both failures were about the encoding of the haystack rather than the
        # behaviour under test. The behaviour under test is that status reports
        # which evidence store it inspected, and that is a named field.
        self.assertEqual(data["scope"]["storesInspected"]["evidenceDir"],
                         self._evidence_dir(),
                         "status must still show the evidence store was inspected")

        # Re-run over the SAME dirty tree; exit code must not depend on
        # whether evidence could be minted or on the tree's cleanliness.
        out2 = self._sbe("verify", self.repo)
        self.assertEqual(out2.returncode, exit_over_dirty,
                         "verify's exit code changed between two runs over the identical tree")

    def test_an_unwritable_evidence_store_leaves_the_exit_code_untouched_and_prints_one_line(self):
        """Acceptance 3: `.sbe` exists as a plain FILE, so `.sbe/evidence`
        cannot be created. Minting fails for all three delegates; the exit
        code must be exactly what it would have been without that failure,
        and the failure must be reported as one line, not one per delegate.
        """
        self._write("README.md", "base\n")
        self._commit("base")
        baseline = self._sbe("verify", self.repo)
        shutil.rmtree(os.path.join(self.repo, ".sbe"), ignore_errors=True)

        self._write(".sbe", "occupying this path so .sbe/evidence cannot be created\n")
        blocked = self._sbe("verify", self.repo)

        self.assertEqual(blocked.returncode, baseline.returncode,
                         "an unwritable evidence store changed verify's exit code")
        self.assertFalse(os.path.isdir(self._evidence_dir()),
                         "no evidence directory should exist when .sbe is a file")
        stderr_lines = [l for l in blocked.stderr.splitlines() if l.strip()]
        mint_lines = [l for l in stderr_lines if "evidence" in l and "sbe verify" in l]
        self.assertEqual(len(mint_lines), 1,
                         "expected exactly one plain stderr line about the failed mint, got "
                         "%d: %r" % (len(mint_lines), stderr_lines))
        self.assertIn("were not minted", mint_lines[0])
        self.assertIn("exit code is unchanged", mint_lines[0])

    def test_repeated_verify_runs_do_not_fail_their_own_evidence_store(self):
        """Acceptance 4. Regenerating the SAME three receipts at their fixed
        paths, committed between runs (the CI re-run shape KNOWN-LIMITS'
        "Evidence covering evidence" describes), must never make one
        receipt FAIL because a sibling receipt regenerated: the only
        acceptable BROKEN CLAIMS reason after committing is the pre-existing,
        already-documented headCommit staleness (a receipt committed
        alongside itself is, by construction, one commit behind), never a
        covered-file hash complaint. Two full rounds are run so the
        exclusion is proven on more than one regeneration."""
        self._write("README.md", "base\n")
        self._commit("base")
        self._write("app.py", "print('hi')\n")
        self._commit("add app")

        for round_ in range(2):
            out = self._sbe("verify", self.repo)
            self.assertIn(out.returncode, (0, 1), out.stdout + out.stderr)
            self._commit("round %d: evidence and decisions" % round_)

        status = self._sbe("status", self.repo, "--json")
        data = json.loads(status.stdout)
        poisoned = [b for b in data["brokenClaims"]
                   if "covered file" in b["finding"] or "now hashes to" in b["finding"]]
        self.assertEqual(poisoned, [],
                         "the evidence store poisoned itself across repeated verify runs: %r"
                         % poisoned)


class TestHelpMeansHelpOnEveryCommand(unittest.TestCase):
    """The whole-surface sweep of the defect class 117744f fixed on the three
    data commands: an explicit help request exited 2 on EVERY subcommand.
    Two mechanisms, both pinned here. The top-level parser built every child
    with add_help=False and argparse's REMAINDER drops a LEADING flag, so
    `sbe intake -h` was refused as a usage error by a parser that had no help
    to give; and the tools behind `design`, `score` and `fences` stripped
    flags wholesale, so run directly, `-h` silently ran a REAL scan instead
    of printing help, the same shape as `data-export --help` running a real
    export. Help is not an error, and a flag a surface does not know is
    refused with exit 2, never silently dropped. Calibrated by reinjecting
    add_help=False plus the argparse-first dispatch in cli.py and the missing
    help branches in the tools: every fixture here went red (a returncode of
    2 or a real scan where usage was asserted) before the fix was restored,
    the restore verified against the pre-recorded `git hash-object` of each
    fixed file."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def _run(self, *argv, **kwargs):
        return subprocess.run([sys.executable, self.SBE] + list(argv),
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL,
                              cwd=kwargs.get("cwd") or ROOT)

    def _commands(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            import brothersbe.cli as cli
            return cli
        finally:
            sys.path.pop(0)

    def test_dash_h_and_dash_dash_help_exit_0_on_every_command(self):
        """Every name in cli.COMMANDS, both spellings, so a command added
        later cannot rejoin the defect class unnoticed."""
        cli = self._commands()
        for name, _help, _run in cli.COMMANDS:
            for flag in ("-h", "--help"):
                out = self._run(name, flag)
                self.assertEqual(out.returncode, 0,
                                 "sbe %s %s exited %d; an explicit help request is "
                                 "not an error: %s"
                                 % (name, flag, out.returncode, out.stdout + out.stderr))
                self.assertIn("usage", (out.stdout + out.stderr).lower(),
                              "sbe %s %s exited 0 but printed no usage: %r"
                              % (name, flag, out.stdout + out.stderr))

    def test_help_on_the_scanning_tools_examines_nothing(self):
        """Run from an empty directory, `-h` on the tools that scan a tree
        must print usage and stop: no report header, no verdict line. Before
        the fix, sbe_design.py and sbe_score.py stripped `-h` and scanned for
        real, and their exit 0 was a scan result, not an answered question."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in ("design", "score", "gate", "plan", "decide", "intake"):
            out = self._run(name, "-h", cwd=tmp)
            self.assertEqual(out.returncode, 0, "sbe %s -h: %s"
                             % (name, out.stdout + out.stderr))
            self.assertNotIn("BROTHERSBE", out.stdout,
                             "sbe %s -h printed a report header, so it ran a real "
                             "scan instead of answering the help request" % name)
            self.assertEqual(os.listdir(tmp), [],
                             "sbe %s -h wrote into the directory it ran from" % name)

    def test_an_unrecognized_flag_refuses_with_exit_2_on_every_command(self):
        cli = self._commands()
        for name, _help, _run in cli.COMMANDS:
            out = self._run(name, "--no-such-flag-on-purpose")
            self.assertEqual(out.returncode, cli.EXIT_USAGE,
                             "sbe %s --no-such-flag-on-purpose exited %d, not the "
                             "usage error %d; a flag a surface does not know must be "
                             "refused, never silently dropped: %s"
                             % (name, out.returncode, cli.EXIT_USAGE,
                                out.stdout + out.stderr))


class TestTheLintCountsNothingTheOperatingSystemLeftBehind(unittest.TestCase):
    """The scorer's unread tally must be a property of the COMMIT, not of the
    machine that ran it.

    Why this exists, in full, because the shape repeats and the cost was real.
    The `silent-failure-lints` verdict carries a sentence naming how many files
    it did not open because no pattern reads their kind. That sentence is
    PASTED INTO TRACKED DOCUMENTATION, and another check compares the pasted
    copy against a live run. On 2026-08-15 a `tools/.DS_Store`, which macOS
    writes the instant somebody opens a folder in Finder, moved that figure
    from 5 to 6. A repair regenerated the guide FROM that tree and baked the 6
    in, so the document matched on one machine and failed in every clean
    checkout. Three sessions then measured one commit and reported three
    different eval results before anyone found the cause, and the check that
    should have pointed at the environment pointed at the document instead.

    The assertion is deliberately a DIFFERENTIAL rather than a fixed number: a
    fixed expected count would itself go stale every time a file is added, which
    is the same class of defect one level up.
    """

    def test_a_finder_artifact_does_not_move_the_unread_count(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        tools_dir = os.path.join(root, "tools")
        artifact = os.path.join(tools_dir, ".DS_Store")
        self.assertFalse(os.path.exists(artifact),
                         "this fixture creates and removes %s; it was already there, so "
                         "the run would not be measuring what it claims" % artifact)

        def unread_line():
            out = subprocess.run([sys.executable, os.path.join(tools_dir, "sbe_score.py"),
                                  "tools/"], cwd=root, capture_output=True, text=True)
            hits = re.findall(r"(\d+) file\(s\) under tools/ were not opened", out.stdout)
            self.assertTrue(hits, "the scorer printed no unread sentence, so this test "
                                  "measured nothing: %s" % out.stdout[-400:])
            return hits[0]

        before = unread_line()
        io.open(artifact, "w").write("")
        try:
            after = unread_line()
        finally:
            os.remove(artifact)
        self.assertEqual(before, after,
                         "a hidden file the operating system wrote moved the unread count "
                         "from %s to %s. That number reaches tracked documentation, so it "
                         "must not depend on whether a file manager opened a directory."
                         % (before, after))


class TestExemptionAddressing(unittest.TestCase):
    """One `.sbe-exempt` format, two scanners, and each used to refuse a file
    addressed only to the other: the gate scanner FAILed the shipped
    templates/dossier exemption (checks: only, no gates:), and the design
    scanner FAILed the teaching dossier's approval waiver (gates: only, no
    checks:), so `--strict .` could not be green with both files well-formed.
    The rule now: a file naming ONLY the other registry's field is addressed
    to that scanner, which honors and PRINTS it, and this scanner skips it;
    a file naming NEITHER field is still refused by both, because that shape
    is an off switch. Calibrated by reinjecting the parsers without the
    addressed-elsewhere branches: the first three fixtures went red (a design
    refusal where exit 0 was asserted, a gate refusal where an approval FAIL
    was asserted, and exit 1 at the repo root), before the fix was restored,
    the restore verified against the pre-recorded `git hash-object` of both
    tools."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))

    def _tool(self, name, *argv):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", name)] + list(argv),
            capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def _estate(self, exempt_body, dossier=False):
        """A directory carrying the exemption and a bare typed APPROVAL. With
        dossier=True it also carries 01-purpose.md, because the design walk
        only reads a .sbe-exempt inside something it recognizes as a dossier."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with io.open(os.path.join(tmp, ".sbe-exempt"), "w", encoding="utf-8") as fh:
            fh.write(exempt_body)
        with io.open(os.path.join(tmp, "APPROVAL"), "w", encoding="utf-8") as fh:
            fh.write("A bare typed name, exactly the shape the approval gate refuses.\n")
        if dossier:
            with io.open(os.path.join(tmp, "01-purpose.md"), "w", encoding="utf-8") as fh:
                fh.write("# Purpose\nA fixture dossier for the exemption-addressing tests.\n")
        return tmp

    def test_a_gates_only_exemption_belongs_to_the_gate_scanner(self):
        body = ("gates: approval\nreason: a shipped teaching fixture, "
                "waived so a whole-tree scan reads pedagogy as WAIVED\n")
        gate = self._tool("sbe_gate.py", "--strict", self._estate(body))
        self.assertEqual(gate.returncode, 0,
                         "the gate scanner must honor its own field: %s" % gate.stdout)
        self.assertIn("WAIVED", gate.stdout)
        design = self._tool("sbe_design.py", "--strict", self._estate(body, dossier=True))
        self.assertNotIn("names no checks", design.stdout,
                         "a gates-only exemption is not design's to refuse: %s"
                         % design.stdout)
        self.assertNotIn(".sbe-exempt that does not exempt anything", design.stdout)

    def test_a_checks_only_exemption_belongs_to_the_design_scanner_and_waives_no_gate(self):
        tmp = self._estate("checks: adr\nreason: kept for history, the decision record "
                           "moved to the archive and this dossier is closed\n")
        gate = self._tool("sbe_gate.py", "--strict", tmp)
        self.assertEqual(gate.returncode, 1,
                         "a checks-only exemption must not waive the approval gate; the "
                         "bare typed APPROVAL under it has to FAIL: %s" % gate.stdout)
        self.assertIn("approval", gate.stdout)
        self.assertNotIn("names no gates", gate.stdout,
                         "the gate scanner refused a file addressed to design")

    def test_an_exemption_naming_neither_field_is_refused_by_both(self):
        body = "reason: three words of filler that switch everything off\n"
        gate = self._tool("sbe_gate.py", "--strict", self._estate(body))
        self.assertEqual(gate.returncode, 1, gate.stdout)
        self.assertIn("names no gates", gate.stdout)
        design = self._tool("sbe_design.py", "--strict", self._estate(body, dossier=True))
        self.assertEqual(design.returncode, 1, design.stdout)
        self.assertIn("names no checks", design.stdout)

    def test_an_expired_exemption_is_refused_not_honored(self):
        """A past `expires:` is a present-but-unusable claim, not an absent
        one (same reasoning as an unreadable receipt: load_receipt's own
        docstring in sbe_gate.py calls that a broken claim rather than an
        absent one), so both parsers refuse it as a FAIL and the artifact it
        would have waived is checked in full instead of silently passing."""
        gate_body = ("gates: approval\nowner: the QC lead\nexpires: 2000-01-01\n"
                     "reason: this dossier was closed and is kept for history "
                     "while the decision record moved to the archive\n")
        gate = self._tool("sbe_gate.py", "--strict", self._estate(gate_body))
        self.assertEqual(gate.returncode, 1,
                         "an expired exemption must FAIL the gate scanner: %s" % gate.stdout)
        self.assertIn("expires", gate.stdout)

        design_body = ("checks: adr\nowner: the QC lead\nexpires: 2000-01-01\n"
                       "reason: this dossier was closed and is kept for history "
                       "while the decision record moved to the archive\n")
        design = self._tool("sbe_design.py", "--strict",
                            self._estate(design_body, dossier=True))
        self.assertEqual(design.returncode, 1,
                         "an expired exemption must FAIL the design scanner: %s"
                         % design.stdout)
        self.assertIn("expires", design.stdout)

    def test_an_exemption_naming_neither_owner_nor_expiry_still_parses_clean(self):
        """Grandfather path: every exemption already in a tree named neither
        field before owner/expiry existed, and none of them may start
        refusing now that the two new keys exist."""
        body = ("gates: approval\nreason: a shipped teaching fixture, "
                "waived so a whole-tree scan reads pedagogy as WAIVED\n")
        gate = self._tool("sbe_gate.py", "--strict", self._estate(body))
        self.assertEqual(gate.returncode, 0, gate.stdout)
        self.assertIn("no owner recorded, no expiry recorded", gate.stdout)

    def test_the_design_parsers_grandfather_path_is_verified_on_its_own(self):
        """Mirror of the grandfather test above, driving the design parser
        (sbe_design.py) instead of the gate parser. The test above only ever
        exercised the gate half of this mirrored pair; the design half's own
        _owner_expiry call site was unverified on its own."""
        body = ("checks: adr\nreason: this dossier was closed in 2024 and is "
                "kept for history, the decision record it named moved to the "
                "archive\n")
        tmp = self._estate(body, dossier=True)
        # T0 owes no artifact (REQUIRED["T0"] in sbe_intake.py is empty), so
        # this stays about the exemption path, not about which artifacts a
        # higher tier would additionally owe.
        with io.open(os.path.join(tmp, "00-intake.json"), "w", encoding="utf-8") as fh:
            fh.write('{"tier": "T0"}')
        design = self._tool("sbe_design.py", "--strict", tmp)
        self.assertEqual(design.returncode, 0, design.stdout)
        self.assertIn("no owner recorded, no expiry recorded", design.stdout)

    def test_exactly_one_of_owner_or_expires_is_refused_by_name_both_directions_both_parsers(self):
        """Half an exception is not an exception. Both directions (owner
        without expires, expires without owner) in both parsers, asserting
        the exact missing-key sentence _owner_expiry returns, not merely
        that a problem was non-empty."""
        reason = ("this dossier was closed and is kept for history while the "
                  "decision record it names moved to the archive\n")

        gate_owner_only = self._tool("sbe_gate.py", "--strict", self._estate(
            "gates: approval\nowner: the QC lead\nreason: %s" % reason))
        self.assertEqual(gate_owner_only.returncode, 1, gate_owner_only.stdout)
        self.assertIn("owner: 'the QC lead' but no expires:", gate_owner_only.stdout)

        gate_expires_only = self._tool("sbe_gate.py", "--strict", self._estate(
            "gates: approval\nexpires: 2099-01-01\nreason: %s" % reason))
        self.assertEqual(gate_expires_only.returncode, 1, gate_expires_only.stdout)
        self.assertIn("expires: '2099-01-01' but no owner:", gate_expires_only.stdout)

        design_owner_only = self._tool("sbe_design.py", "--strict", self._estate(
            "checks: adr\nowner: the QC lead\nreason: %s" % reason, dossier=True))
        self.assertEqual(design_owner_only.returncode, 1, design_owner_only.stdout)
        self.assertIn("owner: 'the QC lead' but no expires:", design_owner_only.stdout)

        design_expires_only = self._tool("sbe_design.py", "--strict", self._estate(
            "checks: adr\nexpires: 2099-01-01\nreason: %s" % reason, dossier=True))
        self.assertEqual(design_expires_only.returncode, 1, design_expires_only.stdout)
        self.assertIn("expires: '2099-01-01' but no owner:", design_expires_only.stdout)

    def test_a_date_shaped_like_basic_format_is_refused_not_silently_honored(self):
        """THE DEFECT this control exists for: datetime.date.fromisoformat on
        Python 3.11+ also accepts basic format (no hyphens), so `20991231`
        parsed clean and was honored as a granted exception, even though the
        refusal message this same function prints promises YYYY-MM-DD. Two
        scanners on two Python minors could then disagree about the same
        file. `20991231` is the exact input the reviewer confirmed was
        honored on this interpreter."""
        gate = self._tool("sbe_gate.py", "--strict", self._estate(
            "gates: approval\nowner: the QC lead\nexpires: 20991231\n"
            "reason: this dossier was closed and is kept for history while "
            "the decision record it names moved to the archive\n"))
        self.assertEqual(gate.returncode, 1,
                         "20991231 is not YYYY-MM-DD and must be refused: %s" % gate.stdout)
        self.assertIn("expires: '20991231' is not a YYYY-MM-DD date", gate.stdout)

        design = self._tool("sbe_design.py", "--strict", self._estate(
            "checks: adr\nowner: the QC lead\nexpires: 20991231\n"
            "reason: this dossier was closed and is kept for history while "
            "the decision record it names moved to the archive\n", dossier=True))
        self.assertEqual(design.returncode, 1,
                         "20991231 is not YYYY-MM-DD and must be refused: %s" % design.stdout)
        self.assertIn("expires: '20991231' is not a YYYY-MM-DD date", design.stdout)

    def test_prose_inside_reason_that_looks_like_a_key_is_kept_as_prose_never_granted(self):
        """THE DEFECT this control exists for, reproduced verbatim from the
        adversarial review: owner:/expires: were matched even AFTER reason:
        was seen, so continuation prose inside a reason ("...ask the\\nowner:
        of the archive, this\\nexpires: 2099-01-01") was lifted out of the
        reason and printed as a GRANTED owner and expiry the file never
        declared. Asserted on the CONTENT of the reason string, not merely
        that the problem is empty, because a problem that is empty here is
        exactly what let the defect through the first time."""
        body = ("gates: approval\nreason: kept for history; ask the\n"
                "owner: of the archive, this\nexpires: 2099-01-01\n")
        gate = self._tool("sbe_gate.py", "--strict", self._estate(body))
        self.assertEqual(gate.returncode, 0, gate.stdout)
        # The prose survives whole, unlifted.
        self.assertIn("kept for history; ask the owner: of the archive, this expires: 2099-01-01",
                      gate.stdout)
        # No owner/expires were actually read out of it: the grandfather
        # suffix appears, never a granted [owner: ...; expires: ...] pair.
        self.assertIn("no owner recorded, no expiry recorded", gate.stdout)
        self.assertNotIn("[owner: of the archive, this; expires: 2099-01-01]", gate.stdout)
        self.assertIn("kept as prose, not a key", gate.stdout)

        design_body = ("checks: adr\nreason: kept for history; ask the\n"
                       "owner: of the archive, this\nexpires: 2099-01-01\n")
        design_tmp = self._estate(design_body, dossier=True)
        with io.open(os.path.join(design_tmp, "00-intake.json"), "w", encoding="utf-8") as fh:
            fh.write('{"tier": "T0"}')
        design = self._tool("sbe_design.py", "--strict", design_tmp)
        self.assertEqual(design.returncode, 0, design.stdout)
        self.assertIn("kept for history; ask the owner: of the archive, this expires: 2099-01-01",
                      design.stdout)
        self.assertIn("no owner recorded, no expiry recorded", design.stdout)
        self.assertIn("kept as prose, not a key", design.stdout)

    def test_this_repository_passes_its_own_root_scan_with_the_pedagogy_waived(self):
        gate = self._tool("sbe_gate.py", "--strict", ROOT)
        self.assertEqual(gate.returncode, 0,
                         "gate --strict over the repo root: %s" % gate.stdout[-2000:])
        self.assertIn("WAIVED", gate.stdout)
        self.assertIn("infra-topology", gate.stdout,
                      "the teaching dossier's approval waiver must be printed, "
                      "never silent")
        design = self._tool("sbe_design.py", "--strict", ROOT)
        self.assertEqual(design.returncode, 0,
                         "design --strict over the repo root: %s" % design.stdout[-2000:])


class TestDoctorIdentityCheck(unittest.TestCase):
    """THE DEFECT THIS CONTROL EXISTS FOR: a leaked fixture identity, `ci
    <ci@example.com>`, authored a run of real commits in public history
    before anyone noticed, because nothing in this project's own tooling
    ever looked at git's identity config. `doctor` now does, and this class
    pins the three shapes that check must never blur: a fixture identity is
    a WARNING (never silently PASS, never a hard FAIL: doctor observes), a
    real identity PASSes, and an unset identity is NO-DATA rather than
    either. Real subprocess, real git config, nothing mocked, the same rule
    `test_sbe_adopt.py` states."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        # B-010's project-init check (see TestDoctorProjectInitCheck below)
        # now FAILs a bare repo, which would trip every `code == 0`
        # assertion in this class for a reason unrelated to identity. This
        # class is about the identity check specifically, so its fixture
        # carries BrotherSBE's own footprint like a real installed project
        # would, the same way `sbe doctor` is meant to be run.
        subprocess.run([sys.executable, self.SBE, "init", self.repo, "--apply"],
                       cwd=self.repo, capture_output=True, text=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _doctor(self, isolate_identity=False):
        """Three values, not two: a two-value return reads as a possible
        (verdict, evidence) pair to the honesty meta-test, which refuses any
        such function sitting outside a check registry."""
        env = dict(os.environ)
        if isolate_identity:
            # A repo with no local identity set must not fall through to
            # this machine's real ~/.gitconfig or /etc/gitconfig, or the
            # unset-identity case could never be reproduced on a laptop
            # that already has a name and email configured.
            env["HOME"] = self.repo
            env["GIT_CONFIG_NOSYSTEM"] = "1"
        out = subprocess.run([sys.executable, self.SBE, "doctor"], cwd=self.repo,
                             capture_output=True, text=True, env=env)
        return out.returncode, out.stdout, out.stdout + out.stderr

    @staticmethod
    def _identity_line(stdout):
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses any
        # such function sitting outside a check registry.
        m = re.search(r"^identity\s+(\S+)\s+(.*)$", stdout, re.M)
        return (m.group(1), m.group(2), m.group(0)) if m else (None, None, None)

    def test_a_leaked_example_com_email_is_a_warning_not_a_failure(self):
        subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=self.repo, check=True)
        code, out, text = self._doctor()
        result, detail, _ = self._identity_line(out)
        self.assertEqual(result, "WARNING", text)
        self.assertIn("ci@example.com", detail, "the evidence line must quote the value "
                                                 "found: %s" % text)
        self.assertEqual(code, 0, "a WARNING must never trip doctor's exit code: %s" % text)

    def test_a_leaked_name_ci_is_a_warning_not_a_failure(self):
        subprocess.run(["git", "config", "user.email", "real@realcompany.com"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "ci"], cwd=self.repo, check=True)
        code, out, text = self._doctor()
        result, detail, _ = self._identity_line(out)
        self.assertEqual(result, "WARNING", text)
        self.assertIn("\"ci\"", detail, "the evidence line must quote the value found: %s"
                      % text)
        self.assertEqual(code, 0, text)

    def test_a_real_identity_passes(self):
        subprocess.run(["git", "config", "user.email", "real@realcompany.com"], cwd=self.repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "Real Person"], cwd=self.repo, check=True)
        code, out, text = self._doctor()
        result, detail, _ = self._identity_line(out)
        self.assertEqual(result, "PASS", text)
        self.assertIn("real@realcompany.com", detail, text)
        self.assertEqual(code, 0, text)

    def test_an_unset_identity_is_no_data_not_a_silent_pass(self):
        code, out, text = self._doctor(isolate_identity=True)
        result, detail, _ = self._identity_line(out)
        self.assertEqual(result, "NO-DATA", text)
        self.assertEqual(code, 0, text)


class TestDoctorProjectInitCheck(unittest.TestCase):
    """LANE C3, B-010, softened for the fresh-install case only: the
    marketplace install path never runs `sbe init`, so a beginner's first
    `/brothersbe:start` lands in an uninitialized repository. That state
    used to read FAIL, which kept the safety invariant but made a normal
    new project open on the word FAIL. `project-init` now reads SETUP, a
    third setup-class verdict, whenever `.brothersbe/config.json` is
    absent, and `_cmd_doctor` folds it into an overall `result` of SETUP.
    The invariant survives: SETUP is not PASS, so the `result` Guided mode
    reads (skills/start/SKILL.md step 1) still can never say PASS while
    the main capability cannot run, and the exit code stays nonzero so an
    exit-only caller (install.sh's run_doctor) cannot see ready either.
    Genuine breakage still reads FAIL and outranks SETUP. Real subprocess,
    real git, real `sbe init`, nothing mocked, the same rule
    `TestDoctorIdentityCheck` above holds to."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _doctor_json(self):
        out = subprocess.run([sys.executable, self.SBE, "doctor", "--json"], cwd=self.repo,
                             capture_output=True, text=True)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            data = None
        return out.returncode, data, out.stdout + out.stderr

    @staticmethod
    def _check(data, name):
        for c in data["checks"]:
            if c["name"] == name:
                return c
        return None

    def test_an_uninitialized_repo_reads_setup_and_never_the_word_fail(self):
        code, data, text = self._doctor_json()
        self.assertIsNotNone(data, "doctor --json did not parse: %s" % text)
        check = self._check(data, "project-init")
        self.assertIsNotNone(check, "doctor carries no project-init check: %s" % text)
        self.assertEqual(check["result"], "SETUP", text)
        self.assertIn(".brothersbe/config.json", check["detail"], text)
        self.assertIn("sbe init", check["detail"], text)
        # A fresh install's first look at this tool must read as a normal
        # new-project state, never as breakage: no FAIL anywhere in the
        # detail, and no repair framing.
        self.assertNotIn("FAIL", check["detail"], text)
        self.assertNotIn("REQUIRED-and-missing", check["detail"], text)

    def test_an_uninitialized_repo_never_reads_pass_overall(self):
        """The invariant B-010 exists for, unchanged by the SETUP
        softening: Guided mode reads `result`, not the per-check list, to
        decide whether it is safe to proceed. A missing footprint must
        keep that field off PASS, now as SETUP rather than FAIL, and the
        exit code must stay nonzero so a caller reading only the exit
        code (install.sh's run_doctor) cannot see ready either."""
        code, data, text = self._doctor_json()
        self.assertEqual(data["result"], "SETUP", text)
        self.assertNotEqual(data["result"], "PASS", text)
        self.assertEqual(code, 1, "a SETUP state must keep doctor's exit code nonzero: %s" % text)

    def test_genuine_breakage_still_reads_fail_and_outranks_setup(self):
        """The softening covers ONLY the not-yet-set-up case. A broken
        install (here: a hooks-wiring copy that does not exist, the same
        fabrication tools/test_sbe_doctor_wiring.py uses) must still read
        FAIL overall, even while project-init reads SETUP beside it."""
        env = dict(os.environ)
        env["SBE_HOOKS_JSON"] = os.path.join(self.repo, "no-such-dir", "hooks.json")
        out = subprocess.run([sys.executable, self.SBE, "doctor", "--json"], cwd=self.repo,
                             capture_output=True, text=True, env=env)
        data = json.loads(out.stdout)
        self.assertEqual(data["result"], "FAIL", out.stdout + out.stderr)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(self._check(data, "hooks-wiring")["result"], "FAIL", out.stdout)
        self.assertEqual(self._check(data, "project-init")["result"], "SETUP", out.stdout)

    def test_running_sbe_init_apply_clears_the_check(self):
        init = subprocess.run([sys.executable, self.SBE, "init", self.repo, "--apply"],
                              capture_output=True, text=True)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        code, data, text = self._doctor_json()
        check = self._check(data, "project-init")
        self.assertIsNotNone(check, text)
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn(".brothersbe/config.json", check["detail"], text)


class TestDoctorInstallIdentityCheck(unittest.TestCase):
    """SBE1 (release-blocking shortlist, 2026-08-23): the installed copy and
    the source repository both claim the same VERSION while running
    different code, so the version string a person checks is a lie. `doctor`
    now carries an `install-identity` check: for every installed copy under
    the plugin cache root, it compares that copy's VERSION and
    CHECKSUMS.sha256 manifest against this source tree's own. Same version,
    same manifest: counted toward PASS. Same version, different manifest:
    FAIL, never silent, because that is the exact lie this check exists to
    catch, naming the offending copy by its directory basename. Different
    version: counted as a lagging install, not a lie. No copies, an
    unreadable cache root, or a copy missing CHECKSUMS.sha256: NO-DATA,
    never PASS. PASS itself is a count summary, not a per-copy listing,
    because `evals/replay_book.py` pastes this check's real output into the
    public `docs/book/` chapters, so no detail string here (PASS, FAIL or
    NO-DATA) may ever carry an absolute path. `SBE_PLUGIN_CACHE_ROOT` is
    this check's own override, the same shape `SBE_HOOKS_JSON` gives
    hooks-wiring, so a test can hand it a fabricated cache without a real
    marketplace install on this machine. Real subprocess, real `bin/sbe`,
    nothing mocked, the same rule `TestDoctorProjectInitCheck` above holds
    to."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    SBE = os.path.join(ROOT, "bin", "sbe")

    def setUp(self):
        with io.open(os.path.join(self.ROOT, "VERSION"), encoding="utf-8") as fh:
            self.source_version = fh.read().strip()
        with io.open(os.path.join(self.ROOT, "CHECKSUMS.sha256"), "rb") as fh:
            self.source_checksums = fh.read()
        self.repo = tempfile.mkdtemp()
        self.cache = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        # Fixture carries BrotherSBE's own footprint, exactly as
        # TestDoctorIdentityCheck's setUp does, so the unrelated project-init
        # check does not FAIL every run in this class for a reason that has
        # nothing to do with install identity.
        subprocess.run([sys.executable, self.SBE, "init", self.repo, "--apply"],
                       cwd=self.repo, capture_output=True, text=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.cache, ignore_errors=True)

    def _make_copy(self, name, version_text, checksums_bytes=None, drop_checksums=False):
        copy_dir = os.path.join(self.cache, name)
        os.makedirs(copy_dir)
        with io.open(os.path.join(copy_dir, "VERSION"), "w", encoding="utf-8") as fh:
            fh.write(version_text)
        if not drop_checksums:
            with open(os.path.join(copy_dir, "CHECKSUMS.sha256"), "wb") as fh:
                fh.write(checksums_bytes if checksums_bytes is not None
                         else self.source_checksums)
        return copy_dir

    def _doctor_json(self):
        env = dict(os.environ)
        env["SBE_PLUGIN_CACHE_ROOT"] = self.cache
        out = subprocess.run([sys.executable, self.SBE, "doctor", "--json"], cwd=self.repo,
                             capture_output=True, text=True, env=env)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            data = None
        return out.returncode, data, out.stdout + out.stderr

    @staticmethod
    def _check(data, name):
        for c in data["checks"]:
            if c["name"] == name:
                return c
        return None

    def test_same_version_same_checksums_passes_with_a_count_summary(self):
        self._make_copy("same", self.source_version)
        code, data, text = self._doctor_json()
        self.assertIsNotNone(data, "doctor --json did not parse: %s" % text)
        check = self._check(data, "install-identity")
        self.assertIsNotNone(check, "doctor carries no install-identity check: %s" % text)
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn("1 installed copies examined", check["detail"], text)
        self.assertIn("1 match this source", check["detail"], text)
        self.assertIn(self.source_version, check["detail"], text)
        self.assertIn("0 are lagging", check["detail"], text)

    def test_same_version_different_checksums_fails_naming_the_defect(self):
        copy_dir = self._make_copy("tampered", self.source_version,
                                    checksums_bytes=self.source_checksums + b"\n# tampered\n")
        code, data, text = self._doctor_json()
        check = self._check(data, "install-identity")
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn(os.path.basename(copy_dir), check["detail"], text)
        self.assertIn("under the plugin cache root", check["detail"], text)
        self.assertIn(self.source_version, check["detail"], text)
        self.assertIn("running different code", check["detail"], text)
        self.assertEqual(code, 1, "a same-version manifest mismatch must trip doctor's "
                                  "exit code: %s" % text)

    def test_different_version_passes_as_a_lagging_install_not_a_lie(self):
        self._make_copy("older", "0.0.1", checksums_bytes=b"stale manifest\n")
        code, data, text = self._doctor_json()
        check = self._check(data, "install-identity")
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn("1 are lagging", check["detail"], text)
        self.assertIn("not lies", check["detail"], text)

    def test_no_installed_copies_is_no_data_not_a_silent_pass(self):
        code, data, text = self._doctor_json()
        check = self._check(data, "install-identity")
        self.assertEqual(check["result"], "NO-DATA", text)
        self.assertIn("the plugin cache root has no installed copies", check["detail"], text)

    def test_a_copy_with_no_checksums_file_is_no_data_never_pass(self):
        copy_dir = self._make_copy("half-installed", self.source_version, drop_checksums=True)
        code, data, text = self._doctor_json()
        check = self._check(data, "install-identity")
        self.assertEqual(check["result"], "NO-DATA", text)
        self.assertIn(os.path.basename(copy_dir), check["detail"], text)
        self.assertIn("under the plugin cache root", check["detail"], text)

    def test_no_detail_string_ever_carries_an_absolute_path(self):
        """The whole point of this check's wording: `evals/replay_book.py`
        pastes doctor's real output verbatim into the public `docs/book/`
        chapters, so a `/Users/...` or `/home/...` path in any of PASS, FAIL
        or NO-DATA would publish one maintainer's home directory. Exercises
        all three results (match, tamper, lagging, missing-checksums,
        empty-cache) and asserts none of their detail strings leak a path,
        the cache root's own absolute path included."""
        self._make_copy("same", self.source_version)
        self._make_copy("tampered", self.source_version,
                        checksums_bytes=self.source_checksums + b"\n# tampered\n")
        self._make_copy("older", "0.0.1", checksums_bytes=b"stale manifest\n")
        self._make_copy("half-installed", self.source_version, drop_checksums=True)
        code, data, text = self._doctor_json()
        check = self._check(data, "install-identity")
        self.assertIsNotNone(check, text)
        detail = check["detail"]
        self.assertNotIn("/Users/", detail, text)
        self.assertNotIn("/home/", detail, text)
        self.assertNotIn(self.cache, detail, text)

        empty_cache = tempfile.mkdtemp()
        try:
            env = dict(os.environ)
            env["SBE_PLUGIN_CACHE_ROOT"] = empty_cache
            out = subprocess.run([sys.executable, self.SBE, "doctor", "--json"], cwd=self.repo,
                                 capture_output=True, text=True, env=env)
            empty_data = json.loads(out.stdout)
            empty_check = self._check(empty_data, "install-identity")
            self.assertIsNotNone(empty_check, out.stdout + out.stderr)
            self.assertNotIn("/Users/", empty_check["detail"], out.stdout + out.stderr)
            self.assertNotIn("/home/", empty_check["detail"], out.stdout + out.stderr)
            self.assertNotIn(empty_cache, empty_check["detail"], out.stdout + out.stderr)
        finally:
            shutil.rmtree(empty_cache, ignore_errors=True)


class TestNoPrivateNameShips(unittest.TestCase):
    """This repository is public. The estates it was built on are not, and
    neither are the clients, employers and projects whose work taught it every
    threshold it ships. A client name reaching a tracked file cannot be taken
    back: a public git history is a permanent record, and the fix after the fact
    is a history rewrite, not an edit.

    The list of names is NOT in this file, for the obvious reason that a
    blocklist naming the client leaks the client. It is read from outside the
    repository, and when it is absent this test SKIPS with a message saying it
    examined nothing, rather than passing. An empty check reporting green is the
    exact failure mode the rest of this project exists to prevent.

    Set BROTHERSBE_PRIVATE_NAMES to a comma-separated list, or
    BROTHERSBE_PRIVATE_NAMES_FILE to a path holding one name per line
    (blank lines and # comments ignored). Keep that file outside this tree.

    One honest narrowing, also stated in PUBLISH-CHECKLIST.md: this scans the
    tracked tree at HEAD. A name that was committed once and deleted later is
    still in the history and this test cannot see it. The forensic history sweep
    stays a manual checklist item for that reason."""

    # Vendored minified code is the one place a short name can appear by pure
    # collision: a four-character name is a near-certain substring of SOME
    # generated identifier in two megabytes of minified JavaScript (the real
    # event: the client name sat inside mermaid's own "mbPxRBl..." motion-blur
    # identifier, flanked by letters on both sides). For exactly these files,
    # a hit counts only when the name stands alone, not flanked by a letter or
    # digit, so a genuinely planted name is still caught while a substring of
    # someone else's identifier is not. Every other file keeps the plain
    # substring rule: prose, code and configs we author have no such
    # collision excuse.
    VENDORED_MINIFIED = frozenset({"docs/book/assets/mermaid.min.js"})

    # The same collision, found again on 2026-08-10 outside any minified file.
    # A four-character name matched 19 occurrences of the ordinary English word
    # "hurry" across shipped prose and HTML, and reported nine tracked files as
    # a leak. Nothing had leaked. The narrowing above was written for one file
    # because the first sighting happened to be in one file, but the cause is
    # the NAME being short, not the file being minified: a four-character token
    # is a substring of ordinary English often enough that the plain rule
    # cannot tell a leak from a word.
    #
    # So the boundary rule now follows the name's length rather than the file's
    # kind. Names shorter than this need to stand alone anywhere, not flanked by
    # a letter or a digit. Longer names keep the plain substring rule, because a
    # six-character client name embedded inside another word is not a collision
    # anyone has seen and treating it as one would weaken the check for no
    # measured reason.
    #
    # The honest narrowing this buys, stated rather than buried: a short name
    # deliberately glued inside a longer word, "my<name>file" shaped, is no longer
    # caught in ordinary files. That is a real reduction in coverage. It is
    # accepted because the alternative is a check that cries wolf on every
    # instance of a common word, and a leak detector nobody believes is worse
    # than one with a stated blind spot. The threshold is a judgement, not a
    # measurement.
    SHORT_NAME_CHARS = 6

    @staticmethod
    def _standalone_hit(body_low, name_low):
        i = body_low.find(name_low)
        while i >= 0:
            before = body_low[i - 1] if i > 0 else ""
            after_i = i + len(name_low)
            after = body_low[after_i] if after_i < len(body_low) else ""
            if not before.isalnum() and not after.isalnum():
                return True
            i = body_low.find(name_low, i + 1)
        return False

    def _names(self):
        raw = os.environ.get("BROTHERSBE_PRIVATE_NAMES", "")
        names = [n.strip() for n in raw.split(",") if n.strip()]
        path = os.environ.get("BROTHERSBE_PRIVATE_NAMES_FILE", "")
        if not path:
            default = os.path.expanduser("~/.brothersbe-private-names")
            if os.path.exists(default):
                path = default
        if path and os.path.exists(path):
            for line in io.open(path, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    names.append(line)
        return sorted(set(names))

    def test_no_session_handover_package_is_tracked(self):
        """Session working state must not ship inside the product.

        `sbe handover` mints one directory per session close. Those are one
        session's notes, nothing in the product reads them back, and .gitignore's
        own runtime section already draws this line: state is ignored, CONFIG
        under .sbe/ (policy.yml, checks.yml, team-profile.json) stays tracked.
        The handover packages were simply never added to that list.

        Measured cost of the gap: four of them, 16 files, shipped inside the
        PUBLIC v3.4.1 tag. Found by auditing the released tag from a clean clone,
        not by any check here, and the reason no check caught it is worth keeping.
        The sibling product shipped ONE stray session note and its doc-truth
        suite failed, because that suite asserts the manifest matches the tagged
        tree. Here the manifest was perfectly correct: the files were tracked, so
        they were in it, and verify-install reported 0 extra. A MANIFEST CANNOT
        TELL YOU A FILE SHOULD NOT HAVE BEEN TRACKED. It answers a different
        question, and answering it correctly is exactly what hid this.

        This test asks the question the manifest cannot."""
        root = os.path.abspath(os.path.join(HERE, ".."))
        listed = subprocess.run(["git", "ls-files", "-z", ".sbe/"], cwd=root,
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        self.assertEqual(
            listed.returncode, 0,
            "git ls-files failed, so the tracked set is unknown; refusing to "
            "report a clean result over a file list nobody could build")
        tracked = [f for f in listed.stdout.split("\0") if f]
        self.assertTrue(
            tracked,
            "git tracks nothing under .sbe/, which is NO-DATA rather than a "
            "pass: the config that belongs there (policy.yml, checks.yml, "
            "team-profile.json) should be tracked, so an empty result means "
            "this check looked in the wrong place")
        shipped = sorted(f for f in tracked if "/handover-" in "/" + f)
        self.assertEqual(
            shipped, [],
            "%d session handover file(s) are tracked and would ship inside the "
            "product: %s. They are one session's working notes. Untrack them "
            "with `git rm -r --cached` (which leaves them on disk and in "
            "history) and confirm .gitignore carries `.sbe/handover-*/`."
            % (len(shipped), ", ".join(shipped[:5])))

    def test_no_private_name_appears_in_a_tracked_file(self):
        names = self._names()
        if not names:
            self.skipTest(
                "no private-name list configured, so NOTHING was scanned. This is NO-DATA, "
                "not a clean result: set BROTHERSBE_PRIVATE_NAMES or "
                "BROTHERSBE_PRIVATE_NAMES_FILE (a path outside this repository) to make this "
                "check real.")
        root = os.path.abspath(os.path.join(HERE, ".."))
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                                capture_output=True, text=True)
        self.assertEqual(listed.returncode, 0,
                         "git ls-files failed, so the scan set is unknown; refusing to report "
                         "a clean scan over a file list nobody could build")
        files = [f for f in listed.stdout.split("\0") if f]
        self.assertTrue(files, "git tracks no files here; that is not a clean scan either")
        hits, scanned = [], 0
        for rel in files:
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                continue
            try:
                body = io.open(full, encoding="utf-8", errors="ignore").read()
            except (IOError, OSError):
                continue
            scanned += 1
            low = body.lower()
            vendored = rel in self.VENDORED_MINIFIED
            for name in names:
                name_low = name.lower()
                boundary_only = vendored or len(name) < self.SHORT_NAME_CHARS
                hit = (self._standalone_hit(low, name_low) if boundary_only
                       else name_low in low)
                if hit:
                    # Print the FILE and the name's length, never the name and
                    # never the surrounding line: a failure message is written
                    # into CI logs, and a leak-detector that prints the leak has
                    # moved the problem rather than caught it.
                    hits.append("%s (private name of %d chars)" % (rel, len(name)))
        self.assertEqual(hits, [],
                         "a private name reached %d tracked file(s): %s. Fix before the next "
                         "push; if it is already pushed, the fix is a history rewrite."
                         % (len(hits), hits))
        self.assertGreater(scanned, 0, "scanned zero files, which is NO-DATA and not a pass")

    def _load_acceptance_entries(self, path):
        """Reads .sbe-private-history-acceptance.json and returns a dict with
        named keys "accepted" and "note", never a bare 2-tuple: a literal
        (x, y) return reads to the honesty meta-test as an unregistered
        (verdict, evidence) pair it cannot prove is never PASS, and this
        helper has no registry entry to prove it in.

        accepted maps a 12-character object id (lowercase) to its reason
        string, for every entry whose reason is non-empty; an entry with an
        empty or missing reason waives nothing (same discipline as the
        silent-failure lint's allow-silent: a waiver needs a stated reason,
        not just a listing). accepted is None when the record is missing,
        unreadable or malformed, which is NO-DATA for the waiver: every hit
        then fails exactly as it did before this record existed, never a
        silent pass. note is a short string describing what was read, always
        safe to print: it never repeats a hit's path or a private term."""
        if not os.path.isfile(path):
            return {"accepted": None,
                    "note": "acceptance record unreadable: not found at %s" % path}
        try:
            body = io.open(path, encoding="utf-8").read()
        except (IOError, OSError) as exc:
            return {"accepted": None, "note": "acceptance record unreadable: %s" % exc}
        try:
            data = json.loads(body)
        except ValueError as exc:
            return {"accepted": None,
                    "note": "acceptance record unreadable: invalid JSON (%s)" % exc}
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return {"accepted": None,
                    "note": "acceptance record unreadable: no 'entries' list"}
        accepted = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            short = entry.get("object_id_short")
            reason = entry.get("reason")
            if isinstance(short, str) and short.strip():
                accepted[short.strip().lower()] = (
                    reason.strip() if isinstance(reason, str) else "")
        return {"accepted": accepted,
                "note": "%d entrie(s) read from %s" % (len(accepted), os.path.basename(path))}

    def test_no_private_name_reaches_publishable_history(self):
        """The gap the tracked-tree scan above cannot close.

        That scan reads the tree at HEAD. A file committed once and deleted
        later is gone from HEAD and still in the history, still copied by every
        clone, and still greppable by anyone who has one. That is not
        hypothetical here: a rendered diagram carrying a four-character name
        survived on a feature branch while the tracked-tree control reported
        clean, because the commit that removed the FILE did not remove the
        BLOB, and the commit that did it was titled "no client and no
        individual is named anywhere in this repository".

        So this reads the objects themselves: every commit MESSAGE and every
        blob reachable from HEAD and from the remote-tracking refs. Two
        deliberate scoping choices, both of which narrow it:

        - Local-only branches are excluded. This project keeps backup/* copies
          of earlier history rewrites on purpose, and a control that is
          permanently red over deliberate local safety copies is a control
          people learn to skip. Local-only TAGS are NOT excluded (added
          2026-09-03, after an audit found two term-bearing blobs reachable
          only from one): a tag is not a branch, and `git push --tags` or
          `--follow-tags` publishes it the same as any other ref.
        - Commit HEADERS are excluded, message bodies only. The author and
          committer lines carry an email domain that is on the name list and
          cannot be changed without rewriting all published history. Gating on
          it would make this permanently red for a condition no edit can fix.
          It is a real exposure and it belongs in a report, not in this gate.

        TWO HONEST LIMITS, stated because a control whose scope is narrower
        than its reputation is the more dangerous kind:

        1. It cannot see the HOST's own refs. GitHub keeps refs/pull/N/head for
           every pull request, permanently, surviving both branch deletion and
           pull request closure, and on a public repository those stay
           fetchable by anyone. Nothing local can enumerate them without the
           network. Removing a name from a branch is therefore NOT the same as
           removing it from the host, and a green run here does not say it is.
        2. It reads what THIS clone has. A ref nobody fetched is not examined,
           so run `git fetch --all --prune` before trusting a green verdict.
        """
        names = self._names()
        if not names:
            self.skipTest(
                "no private-name list configured, so NOTHING was scanned. This is NO-DATA, "
                "not a clean result: set BROTHERSBE_PRIVATE_NAMES or "
                "BROTHERSBE_PRIVATE_NAMES_FILE (a path outside this repository) to make this "
                "check real.")
        root = os.path.abspath(os.path.join(HERE, ".."))
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)",
                               "refs/remotes", "refs/tags"],
                              cwd=root, capture_output=True, text=True)
        self.assertEqual(refs.returncode, 0,
                         "git for-each-ref failed, so the publishable ref set is unknown; "
                         "refusing to report a clean scan over refs nobody could list")
        ref_list = [r for r in refs.stdout.split("\n") if r.strip()] + ["HEAD"]
        listed = subprocess.run(["git", "rev-list", "--objects"] + ref_list,
                                cwd=root, capture_output=True, text=True)
        self.assertEqual(listed.returncode, 0,
                         "git rev-list failed, so the object set is unknown; refusing to "
                         "report a clean scan over objects nobody could enumerate")
        paths = {}
        for line in listed.stdout.split("\n"):
            part = line.split(" ", 1)
            if part[0]:
                paths[part[0]] = part[1] if len(part) > 1 else ""
        self.assertTrue(paths,
                        "nothing is reachable from HEAD or any remote-tracking ref, which is "
                        "NO-DATA and not a clean scan")
        proc = subprocess.Popen(["git", "cat-file", "--batch", "--buffer"], cwd=root,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        raw, _ = proc.communicate(("\n".join(paths) + "\n").encode("utf-8"))
        self.assertEqual(proc.returncode, 0,
                         "git cat-file failed, so the objects were never read; that is "
                         "NO-DATA, not a pass")
        hits, scanned = [], 0
        i = 0
        while i < len(raw):
            end = raw.find(b"\n", i)
            if end < 0:
                break
            header = raw[i:end].decode("utf-8", "replace").split()
            i = end + 1
            if len(header) < 3 or not header[2].isdigit():
                continue
            sha, kind, size = header[0], header[1], int(header[2])
            body = raw[i:i + size]
            i += size + 1
            if kind not in ("blob", "commit"):
                continue
            text = body.decode("utf-8", "replace")
            if kind == "commit":
                # Message only. The headers carry an identity, not a mention.
                split = text.find("\n\n")
                text = text[split + 2:] if split >= 0 else ""
            scanned += 1
            low = text.lower()
            where = paths.get(sha, "")
            vendored = where in self.VENDORED_MINIFIED
            for name in names:
                name_low = name.lower()
                boundary_only = vendored or len(name) < self.SHORT_NAME_CHARS
                hit = (self._standalone_hit(low, name_low) if boundary_only
                       else name_low in low)
                if hit:
                    # Same discipline as the scan above: the FAILURE MESSAGE is
                    # written into logs, so it names the object and the name's
                    # LENGTH, never the name and never the surrounding line.
                    hits.append((kind, sha, where, len(name)))

        record_path = os.path.join(root, ".sbe-private-history-acceptance.json")
        loaded = self._load_acceptance_entries(record_path)
        accepted, record_note = loaded["accepted"], loaded["note"]

        waived, failing = [], []
        for kind, sha, where, name_len in hits:
            short = sha[:12]
            entry = ("%s %s%s (private name of %d chars)"
                     % (kind, short, (" at " + where) if where else "", name_len))
            reason = accepted.get(short, "") if accepted is not None else ""
            if reason:
                waived.append(entry + " -- WAIVED by %s: %s"
                              % (os.path.basename(record_path), reason))
            else:
                failing.append(entry)

        if waived:
            # A waiver records an exposure, it does not clean one; this must
            # never read as a bare green, so the count and the record file
            # go to stderr on every run, pass or fail.
            sys.stderr.write(
                "WAIVED %d publishable-history hit(s) via %s (%s):\n%s\n"
                % (len(waived), os.path.basename(record_path), record_note,
                   "\n".join("  - " + w for w in waived)))

        self.assertEqual(failing, [],
                         "a private name is reachable from publishable history in %d "
                         "object(s) not waived by %s (%s): %s. Deleting the file does not "
                         "remove the blob; the fix is to drop the object from every ref that "
                         "reaches it, or add a reasoned entry to the acceptance record, and "
                         "note that the host may keep pull-request refs this check cannot see."
                         % (len(failing), os.path.basename(record_path), record_note, failing))
        self.assertGreater(scanned, 0,
                           "scanned zero objects, which is NO-DATA and not a pass")

    def test_the_boundary_rule_still_catches_a_planted_standalone_name(self):
        """Calibration, with a synthetic name (the real list never appears in
        a fixture): the vendored-file narrowing must NOT excuse a name that
        stands alone in minified bytes. Only letter-flanked substrings of a
        longer identifier are excused."""
        body = 'var a=1;const zqv4="x";b.motionBlur=2;'.lower()
        self.assertTrue(self._standalone_hit(body, "zqv4"))

    def test_a_short_name_inside_an_ordinary_word_is_not_a_hit(self):
        """The 2026-08-10 false positive, pinned so it cannot come back.

        A four-character synthetic name flanked by letters is a word, not a
        leak. The real instance was a name sitting inside "hurry" across nine
        tracked files, which reported a leak on a tree that had none."""
        for word in ("zqv4y", "azqv4", "somezqv4thing"):
            self.assertFalse(self._standalone_hit(word.lower(), "zqv4"),
                             "%r is a longer word, not a standalone name" % word)

    def test_a_short_name_standing_alone_is_still_a_hit(self):
        """The other half, so the narrowing above cannot silently excuse a real
        leak: the same short name IS caught when it stands on its own, whatever
        punctuation or whitespace surrounds it."""
        # path_zqv4_file, E37 2026-09-03: the underscore-on-both-sides
        # spelling the other three scanners in this estate used to miss;
        # isalnum has always treated it as a boundary here, pinned so it stays.
        for body in ("the zqv4 account", "path/zqv4/file", '"zqv4"', "zqv4",
                     "zqv4_analysis", "path_zqv4_file", "see zqv4."):
            self.assertTrue(self._standalone_hit(body.lower(), "zqv4"),
                            "%r stands the name alone and must be caught" % body)

    def test_a_long_name_keeps_the_plain_substring_rule(self):
        """The threshold applies to SHORT names only. A six-character name is
        long enough that an embedded occurrence has never been a collision here,
        so it keeps the stricter plain rule and this test records that choice."""
        self.assertGreaterEqual(len("zqv4xy"), self.SHORT_NAME_CHARS)
        self.assertLess(len("zqv4"), self.SHORT_NAME_CHARS)

    def test_the_boundary_rule_excuses_only_a_flanked_substring(self):
        body = "n.mbpxrblzqv4red=1,n.clearing=2;".lower()
        self.assertFalse(self._standalone_hit(body, "zqv4"))

    def test_non_vendored_files_keep_the_plain_substring_rule(self):
        """The narrowing is scoped to the vendored list by construction: the
        scan chooses the matcher from VENDORED_MINIFIED membership, so this
        pins that list to exactly one file. Widening it is a decision, not a
        drive-by."""
        self.assertEqual(sorted(self.VENDORED_MINIFIED),
                         ["docs/book/assets/mermaid.min.js"])


class TestPluginSurface(unittest.TestCase):
    """The plugin packaging and the law it packages are two surfaces that can
    drift apart in silence: a skill can cite a reference file that was renamed,
    a hook can point at a tool that moved, the manifest version can wander away
    from VERSION, and the plugin still loads perfectly in every one of those
    cases. Loading is not the property worth asserting. These tests assert the
    ones that are, by reading the claims out of the shipped files rather than
    hardcoding a second copy of them here."""

    ROOT = os.path.join(HERE, "..")

    def _frontmatter(self, path):
        """Return the YAML-ish frontmatter block of a skill or agent file as a
        dict of the top-level scalar keys. Deliberately not a YAML parser: the
        loader needs name and description, and a hand-rolled reader keeps this
        suite dependency-free the way the rest of it is.

        Returns the dict alone, not a (dict, body) pair. It used to return the
        pair, and `evals/test_no_data_class.py` correctly refused it: a function
        returning a two-tuple whose first element could be a verdict, sitting in
        no registry, is indistinguishable to that lint from a check nothing can
        reach. The body was unused by every caller anyway."""
        body = io.open(path, encoding="utf-8").read()
        self.assertTrue(body.startswith("---\n"),
                        "%s does not open with a frontmatter block" % path)
        end = body.find("\n---\n", 3)
        self.assertGreater(end, 0, "%s has an unterminated frontmatter block" % path)
        out = {}
        for line in body[4:end].split("\n"):
            m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
        return out

    def test_manifest_parses_and_agrees_with_the_version_file(self):
        manifest = json.load(io.open(os.path.join(ROOT, ".claude-plugin", "plugin.json"),
                                     encoding="utf-8"))
        self.assertEqual(manifest.get("name"), "brothersbe",
                         "the plugin name is the skill namespace; changing it renames every "
                         "/brothersbe: command without warning anyone")
        version = io.open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
        self.assertEqual(manifest.get("version"), version,
                         "plugin.json says %s and VERSION says %s; `claude plugin tag` "
                         "validates that they agree, so a release cut from this state fails"
                         % (manifest.get("version"), version))
        for field in ("description", "repository", "license"):
            self.assertTrue(manifest.get(field), "plugin.json is missing %s" % field)

    def test_every_skill_declares_the_frontmatter_the_loader_reads(self):
        skills = sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))
        self.assertGreaterEqual(len(skills), 6,
                                "expected the six namespaced skills, found %d" % len(skills))
        for path in skills:
            front = self._frontmatter(path)
            directory = os.path.basename(os.path.dirname(path))
            self.assertEqual(front.get("name"), directory,
                             "%s declares name '%s' but sits in skills/%s; the loader uses the "
                             "directory, so the two must not disagree"
                             % (path, front.get("name"), directory))
            self.assertTrue(front.get("description"),
                            "%s has no description; a skill with no description is a skill "
                            "nothing will ever route to" % path)

    # The one agent allowed to carry write tools. LT-101 added the repository's
    # first non-reviewer agent, and this test fired exactly as its docstring
    # promised it would. The allowlist is by exact stem, not by pattern, so a
    # new writer agent is a deliberate one-line change here, reviewed in the
    # diff, never an accident of naming.
    WRITER_AGENTS = ("implementation-worker",)

    def test_every_agent_declares_itself_and_stays_read_only(self):
        """The reviewer agents claim to be read-only in their own prose. A claim
        in prose is not a restriction, but banning Write, Edit, MultiEdit and
        NotebookEdit from the tools list IS one: an agent that grows any of
        those four has to change this test, which is the moment somebody
        notices. [checked: tool] for those four, and only those four.

        Bash is not banned here, and six of the seven reviewers keep it: their
        own instructions call for git history, timestamps, row counts, test
        runs or dependency checks that Read, Grep and Glob cannot do alone.
        Bash can also write a file (`>`, `rm`, `sed -i`), and nothing in this
        test or in `tools` stops that. Read-only for a Bash-bearing reviewer
        is [human]: a stated discipline in the agent's own prose, not a
        mechanical control. principal-architect is the one reviewer with no
        command, timestamp, or test-run need in its instructions, so it
        carries no Bash at all, closing that gap for itself specifically
        rather than leaving an unused write vector on the claim that the
        agent merely does not choose to use it.

        The evidence auditor matters most among the Bash-bearing six, because
        an auditor that can write the evidence it approves is not an auditor,
        and its read-only claim rests on the same [human] footing as the
        other five. A writer agent is legal ONLY when named in WRITER_AGENTS
        above, and it must say what it is in its first two words: a worker
        that could be mistaken for a reviewer is exactly the confusion the
        lean plan forbids."""
        write_tools = ("Write", "Edit", "MultiEdit", "NotebookEdit")
        agents = sorted(glob.glob(os.path.join(ROOT, "agents", "*.md")))
        reviewers = [p for p in agents
                     if os.path.splitext(os.path.basename(p))[0] not in self.WRITER_AGENTS]
        self.assertGreaterEqual(len(reviewers), 7,
                                "expected seven reviewer agents, found %d" % len(reviewers))
        for path in agents:
            front = self._frontmatter(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            self.assertEqual(front.get("name"), stem,
                             "%s declares name '%s'" % (path, front.get("name")))
            self.assertTrue(front.get("description"), "%s has no description" % path)
            tools = front.get("tools", "")
            self.assertTrue(tools, "%s declares no tools list" % path)
            if stem in self.WRITER_AGENTS:
                self.assertTrue(front.get("description", "").startswith("Implementation worker"),
                                "%s carries write tools, so its description must open with "
                                "'Implementation worker'; it opens with %r"
                                % (path, front.get("description", "")[:40]))
                continue
            for banned in write_tools:
                self.assertNotIn(banned, tools,
                                 "%s is documented as read-only but declares %s"
                                 % (path, banned))

    def test_no_frontmatter_value_can_break_the_yaml_parser(self):
        """Found the hard way, by `claude plugin validate` rather than by this
        suite: a skill description containing a colon followed by a space is not
        a valid YAML plain scalar, and the failure is silent at runtime. The
        skill still loads, with EMPTY metadata, so nothing routes to it and no
        error is printed anywhere. The first version of this test class read the
        frontmatter with a regex and happily accepted the broken file, which is
        why the rule is asserted here directly rather than left to the reader:
        a value holding ': ' must be quoted. Same for a leading '[', '{', '&',
        '*' or '!', which start YAML structures rather than prose."""
        starts_structure = ("[", "{", "&", "*", "!", "%", "@", "`")
        for path in (sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))
                     + sorted(glob.glob(os.path.join(ROOT, "agents", "*.md")))):
            body = io.open(path, encoding="utf-8").read()
            end = body.find("\n---\n", 3)
            for line in body[4:end].split("\n"):
                m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
                if not m:
                    continue
                key, value = m.group(1), m.group(2).strip()
                if not value:
                    continue
                quoted = ((value[0] == '"' and value[-1] == '"')
                          or (value[0] == "'" and value[-1] == "'"))
                if quoted:
                    continue
                self.assertNotIn(": ", value,
                                 "%s: the %s value holds a colon and is unquoted, so the YAML "
                                 "parser drops the whole frontmatter and the skill loads with "
                                 "no metadata at all" % (path, key))
                if value[0] in starts_structure and not value.startswith("["):
                    self.fail("%s: the %s value starts with %r, which YAML reads as structure "
                              "rather than text; quote it" % (path, key, value[0]))

    def test_every_hook_command_points_at_a_file_that_exists(self):
        hooks = json.load(io.open(os.path.join(ROOT, "hooks", "hooks.json"),
                                  encoding="utf-8"))
        commands = []
        for event, blocks in hooks["hooks"].items():
            for block in blocks:
                for hook in block["hooks"]:
                    commands.append((event, hook["command"]))
        self.assertTrue(commands, "hooks.json wires nothing")
        for event, command in commands:
            for cited in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)", command):
                self.assertTrue(os.path.exists(os.path.join(ROOT, cited)),
                                "the %s hook runs %s, which does not exist" % (event, cited))

    def test_every_plugin_root_path_cited_by_a_skill_or_agent_resolves(self):
        """This is the drift check. Six skills and seven agents cite the law
        files, the tools and the templates by path. Renaming any of them leaves
        a plugin that still validates and still loads, pointing at nothing."""
        cited = []
        for path in (sorted(glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))
                     + sorted(glob.glob(os.path.join(ROOT, "agents", "*.md")))):
            body = io.open(path, encoding="utf-8").read()
            for ref in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)", body):
                cited.append((path, ref.rstrip(".,)`")))
        self.assertTrue(cited, "no skill or agent cites the plugin root at all")
        missing = [(p, r) for (p, r) in cited
                   if not os.path.exists(os.path.join(ROOT, r))]
        self.assertEqual(missing, [],
                         "these citations resolve to nothing: %s" % missing)


class TestCaptureDefaultsAndAutosaveContentScan(unittest.TestCase):
    """Two privacy defects an external review found, and the fixtures that hold
    them closed.

    The first: this tool parsed the session transcript and stored excerpts of
    the operator's own messages by DEFAULT, with best-effort redaction standing
    between a customer name and a file on disk. A repository holding customer,
    partner, security or company-confidential material has to be able to install
    this and have it capture nothing until somebody says otherwise, per
    category, with an organization switch that cannot be reversed locally.

    The second: the autosave excluded secret-shaped file NAMES, and a secret in
    a normally named source file (`src/config.py` holding an API key) matched no
    name pattern and became a permanent git object. The scan now reads CONTENT
    before `git add` runs, which is the moment a blob would be created.

    Every fixture here runs the real tools in a temporary vault and a real git
    repository. The environment is scrubbed of every BROTHERSBE_ variable and
    the organization policy is pinned at a path that does not exist, so a real
    policy file or an exported switch on the machine running these cannot decide
    the result."""

    TEL = os.path.join(HERE, "sbe_telemetry.py")
    AUTOSAVE = os.path.join(HERE, "sbe_autosave.py")
    SECRET_LINE = 'API_KEY = "sk-ant-api03-ABCDEFGHIJKLMNOP"\n'
    OPERATOR_TEXT = "no, that is not what i asked; always use the acme staging bucket"

    def _env(self, vault, **switches):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHERSBE_")}
        env["BROTHERSBE_VAULT"] = vault
        env["BROTHERSBE_TELEMETRY_POLICY"] = os.path.join(vault, "policy-that-does-not-exist")
        env.update(switches)
        return env

    def _transcript(self, path):
        """A session over the activity floor, carrying one correction-shaped
        operator message and one tool command with a client path in it."""
        msgs = [{"type": "user", "message": {"content": self.OPERATOR_TEXT}}]
        for i in range(bm.MIN_API_MSGS):
            body = [{"type": "text", "text": "planning the acme migration"}]
            if i == 0:
                body.append({"type": "tool_use", "name": "Bash",
                             "input": {"command": "ls /srv/acme-partner"}})
            msgs.append({"type": "assistant",
                         "message": {"id": "m%d" % i, "model": "claude-test",
                                     "usage": {"input_tokens": 10, "output_tokens": 20},
                                     "content": body}})
        io.open(path, "w").write("\n".join(json.dumps(m) for m in msgs) + "\n")
        return path

    def _fire(self, vault, subcommand, cwd, **switches):
        """Run one hook subcommand against a fresh transcript. Returns the
        completed process, so its exit code and output are inspectable rather
        than discarded."""
        tp = self._transcript(os.path.join(vault, "transcript.jsonl"))
        payload = json.dumps({"transcript_path": tp, "cwd": cwd, "session_id": "sess-abc123"})
        return subprocess.run([sys.executable, self.TEL, subcommand], input=payload,
                              text=True, capture_output=True, env=self._env(vault, **switches))

    def _paths(self, vault):
        tel = os.path.join(vault, "99-System", "telemetry")
        return (tel, os.path.join(tel, "outcomes.jsonl"), os.path.join(tel, "corrections.jsonl"))

    def _vault(self):
        v = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, v, True)
        os.makedirs(os.path.join(v, "99-System", "telemetry"))
        return v

    def _snapshot(self, path):
        """Hash of everything under `path`: its own existence, every file's
        relative name, mtime and size. "the file I expected is still there"
        misses a sibling file a run wrote and forgot to check for; hashing the
        whole listing does not need to know in advance what could change."""
        if not os.path.exists(path):
            return "ABSENT"
        rows = []
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                p = os.path.join(root, name)
                st = os.stat(p)
                rows.append("%s %d %d" % (os.path.relpath(p, path), st.st_mtime_ns, st.st_size))
        rows.sort()
        return hashlib.sha256(("\n".join(rows)).encode()).hexdigest()

    def _brief(self, vault):
        tel = os.path.join(vault, "99-System", "telemetry")
        briefs = [f for f in os.listdir(tel) if f.startswith("last-resume-")]
        self.assertEqual(len(briefs), 1, "expected one resume brief, found %r" % briefs)
        return io.open(os.path.join(tel, briefs[0])).read()

    def _no_brief(self, vault, msg):
        tel = os.path.join(vault, "99-System", "telemetry")
        briefs = [f for f in os.listdir(tel) if f.startswith("last-resume-")]
        self.assertEqual(briefs, [], "%s: found %r" % (msg, briefs))

    def test_a_default_installation_captures_no_transcript_text_and_no_correction(self):
        vault = self._vault()
        tel, ledger, corrections = self._paths(vault)
        end = self._fire(vault, "outcomes-append", "/tmp/acme-backend")
        self.assertEqual(end.returncode, 0, end.stderr)
        self.assertFalse(os.path.exists(corrections),
                         "a default installation wrote %s" % corrections)
        self.assertFalse(os.path.exists(ledger), "a default installation wrote %s" % ledger)
        for var in ("BROTHERSBE_TELEMETRY_METRICS", "BROTHERSBE_TELEMETRY_CORRECTIONS"):
            self.assertIn(var, end.stdout,
                          "the hook recorded nothing without naming %s: %s" % (var, end.stdout))
        # transcript-brief opt-in flip (founder, 2026-07-29): the default now
        # writes no brief at all; the switch that would fill it in is named
        # once on stderr instead of inside a withheld placeholder file.
        pre = self._fire(vault, "precompact-brief", "/tmp/acme-backend")
        self.assertEqual(pre.returncode, 0, pre.stderr)
        self._no_brief(vault, "a default installation wrote a resume brief")
        self.assertIn("BROTHERSBE_TELEMETRY_TRANSCRIPT", pre.stderr,
                      "the withheld path does not name the switch that would have written it")

    def test_each_switch_turns_on_exactly_one_category(self):
        cases = (
            ("BROTHERSBE_TELEMETRY_METRICS", True, False, False),
            ("BROTHERSBE_TELEMETRY_CORRECTIONS", False, True, False),
            ("BROTHERSBE_TELEMETRY_TRANSCRIPT", False, False, True),
        )
        for var, want_ledger, want_corrections, want_text in cases:
            vault = self._vault()
            _tel, ledger, corrections = self._paths(vault)
            end = self._fire(vault, "outcomes-append", "/tmp/acme-backend", **{var: "1"})
            self.assertEqual(end.returncode, 0, end.stderr)
            self.assertEqual(os.path.exists(ledger), want_ledger,
                             "%s=1 got outcomes.jsonl exists=%s, wanted %s (%s)"
                             % (var, os.path.exists(ledger), want_ledger, end.stdout))
            self.assertEqual(os.path.exists(corrections), want_corrections,
                             "%s=1 got corrections.jsonl exists=%s, wanted %s (%s)"
                             % (var, os.path.exists(corrections), want_corrections, end.stdout))
            if want_corrections:
                rows = [json.loads(l) for l in io.open(corrections) if l.strip()]
                self.assertTrue(any("staging bucket" in r.get("text", "") for r in rows),
                                "corrections capture is on and captured nothing: %r" % rows)
            pre = self._fire(vault, "precompact-brief", "/tmp/acme-backend", **{var: "1"})
            self.assertEqual(pre.returncode, 0, pre.stderr)
            if want_text:
                body = self._brief(vault)
                self.assertIn("staging bucket", body,
                              "%s=1 turned on transcript capture but the brief has no text" % var)
            else:
                self._no_brief(vault, "%s=1 must not write a resume brief (only %s does)"
                               % (var, bm.CAPTURE_SWITCH["transcript"]))

    def test_the_organization_override_forces_every_category_off(self):
        every_switch = {"BROTHERSBE_TELEMETRY_METRICS": "1",
                        "BROTHERSBE_TELEMETRY_CORRECTIONS": "1",
                        "BROTHERSBE_TELEMETRY_TRANSCRIPT": "1"}
        # (a) the environment override
        vault = self._vault()
        _tel, ledger, corrections = self._paths(vault)
        switches = dict(every_switch, BROTHERSBE_TELEMETRY_DISABLE="1")
        end = self._fire(vault, "outcomes-append", "/tmp/acme-backend", **switches)
        self.assertFalse(os.path.exists(ledger), "a local switch beat the environment override")
        self.assertFalse(os.path.exists(corrections),
                         "a local switch beat the environment override")
        self.assertIn("BROTHERSBE_TELEMETRY_DISABLE", end.stdout,
                      "the override fired without naming itself: %s" % end.stdout)
        pre = self._fire(vault, "precompact-brief", "/tmp/acme-backend", **switches)
        self.assertEqual(pre.returncode, 0, pre.stderr)
        self._no_brief(vault, "a local switch beat the environment override in the resume brief")
        self.assertIn("BROTHERSBE_TELEMETRY_DISABLE", pre.stderr,
                      "the override fired without naming itself in the resume brief path: %s"
                      % pre.stderr)
        # (b) the policy file, which is the half a local shell cannot unset
        vault = self._vault()
        _tel, ledger, corrections = self._paths(vault)
        policy = os.path.join(vault, "telemetry-policy.conf")
        io.open(policy, "w").write("# set by the platform team\ncapture = off\n")
        switches = dict(every_switch, BROTHERSBE_TELEMETRY_POLICY=policy)
        end = self._fire(vault, "outcomes-append", "/tmp/acme-backend", **switches)
        self.assertFalse(os.path.exists(ledger), "a local switch beat the policy file")
        self.assertFalse(os.path.exists(corrections), "a local switch beat the policy file")
        self.assertIn(policy, end.stdout,
                      "the policy file decided the run without being named: %s" % end.stdout)
        # (c) a policy file this version cannot read fails CLOSED
        vault = self._vault()
        _tel, ledger, corrections = self._paths(vault)
        broken = os.path.join(vault, "broken-policy.conf")
        io.open(broken, "w").write("capture = maybe\n")
        end = self._fire(vault, "outcomes-append", "/tmp/acme-backend",
                         **dict(every_switch, BROTHERSBE_TELEMETRY_POLICY=broken))
        self.assertFalse(os.path.exists(ledger),
                         "an unreadable policy directive let capture proceed")
        self.assertIn("fails closed", end.stdout, end.stdout)

    # -- the autosave half -------------------------------------------------

    def _git(self, repo):
        def git(*a):
            return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
        return git

    def _repo(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        git = self._git(repo)
        git("init", "-q")
        git("config", "user.email", "t@t.t")
        git("config", "user.name", "t")
        os.makedirs(os.path.join(repo, "src"))
        io.open(os.path.join(repo, "src", "app.py"), "w").write("def f():\n    return 1\n")
        git("add", "-A")
        git("commit", "-qm", "init")
        return repo

    def test_a_secret_in_a_normally_named_source_file_never_becomes_a_git_object(self):
        repo = self._repo()
        git = self._git(repo)
        vault = self._vault()
        # A file no name pattern will ever match, holding an API key, beside
        # real unlanded work that MUST still be saved.
        io.open(os.path.join(repo, "src", "config.py"), "w").write(self.SECRET_LINE)
        io.open(os.path.join(repo, "wip.txt"), "w").write("UNLANDED-WORK")
        fired = subprocess.run([sys.executable, self.AUTOSAVE, "precompact"],
                               input=json.dumps({"cwd": repo}), text=True,
                               capture_output=True, env=self._env(vault))
        self.assertEqual(fired.returncode, 0, fired.stderr)
        refs = git("for-each-ref", "--format=%(refname)", "refs/brothersbe/autosave").stdout.split()
        self.assertEqual(len(refs), 1, "expected one autosave ref, got %r" % refs)
        listed = git("ls-tree", "-r", "--name-only", refs[0]).stdout.split()
        self.assertNotIn("src/config.py", listed,
                         "the secret-bearing source file entered the snapshot tree")
        self.assertIn("wip.txt", listed, "the content scan dropped the work it exists to save")
        # The stronger claim, and the one the brief asks for: no git OBJECT for
        # that content exists anywhere in the repository, so it was never
        # written rather than written and then hidden from the tree.
        sha = git("hash-object", os.path.join(repo, "src", "config.py")).stdout.strip()
        self.assertTrue(sha, "could not compute the blob id of the planted file")
        present = git("cat-file", "-e", sha)
        self.assertNotEqual(present.returncode, 0,
                            "a blob for the secret-bearing file exists in the object database "
                            "(%s); the scan ran after git add, not before it" % sha)

    def test_the_exclusion_record_names_what_was_excluded_and_why(self):
        repo = self._repo()
        vault = self._vault()
        io.open(os.path.join(repo, "src", "config.py"), "w").write(self.SECRET_LINE)
        io.open(os.path.join(repo, "blob.dat"), "wb").write(b"pre\x00post")
        io.open(os.path.join(repo, "big.txt"), "w").write("x" * 4096)
        fired = subprocess.run([sys.executable, self.AUTOSAVE, "precompact"],
                               input=json.dumps({"cwd": repo}), text=True, capture_output=True,
                               env=self._env(vault, BROTHERSBE_AUTOSAVE_MAX_BYTES="1024"))
        self.assertEqual(fired.returncode, 0, fired.stderr)
        record = os.path.join(vault, "99-System", "telemetry", "autosave-exclusions.log")
        self.assertTrue(os.path.exists(record), "no exclusion record was written at all")
        body = io.open(record).read()
        for path, reason in (("src/config.py", "content matched a secret shape"),
                             ("blob.dat", "binary content"),
                             ("big.txt", "past the 1024 byte limit")):
            self.assertIn(path, body, "%s was dropped without being named" % path)
            self.assertIn(reason, body, "%s was named without a reason" % path)
        self.assertNotIn("sk-ant-api03", body,
                         "the exclusion record wrote down the secret it excluded")
        self.assertIn("scanned", body, "the record does not say how many files it scanned")

    def test_autosave_is_opt_in_in_a_declared_production_repository(self):
        repo = self._repo()
        git = self._git(repo)
        vault = self._vault()
        io.open(os.path.join(repo, ".brothersbe-production"), "w").write("")
        io.open(os.path.join(repo, "wip.txt"), "w").write("UNLANDED-WORK")
        off = subprocess.run([sys.executable, self.AUTOSAVE, "precompact"], input=json.dumps({"cwd": repo}),
                             text=True, capture_output=True, env=self._env(vault))
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertEqual([], git("for-each-ref", "--format=%(refname)",
                                 "refs/brothersbe/autosave").stdout.split(),
                         "a production repository was snapshotted without being opted in")
        log = io.open(os.path.join(vault, "99-System", "telemetry", "autosave.log")).read()
        self.assertIn("BROTHERSBE_AUTOSAVE_PRODUCTION", log,
                      "the skip does not name the switch that would enable it: %s" % log)
        on = subprocess.run([sys.executable, self.AUTOSAVE, "precompact"], input=json.dumps({"cwd": repo}),
                            text=True, capture_output=True,
                            env=self._env(vault, BROTHERSBE_AUTOSAVE_PRODUCTION="1"))
        self.assertEqual(on.returncode, 0, on.stderr)
        refs = git("for-each-ref", "--format=%(refname)", "refs/brothersbe/autosave").stdout.split()
        self.assertEqual(len(refs), 1, "opting in did not produce a snapshot: %r" % refs)

    # -- see it, take it, delete it ----------------------------------------

    def test_show_export_and_purge_do_what_they_claim(self):
        vault = self._vault()
        tel, ledger, corrections = self._paths(vault)
        stored = self._fire(vault, "outcomes-append", "/tmp/acme-backend",
                            BROTHERSBE_TELEMETRY_METRICS="1",
                            BROTHERSBE_TELEMETRY_CORRECTIONS="1")
        self.assertEqual(stored.returncode, 0, stored.stderr)
        self.assertTrue(os.path.exists(ledger) and os.path.exists(corrections),
                        "the fixture stored nothing to show, export or purge: %s" % stored.stdout)

        shown = subprocess.run([sys.executable, self.TEL, "data-show"],
                               capture_output=True, text=True, env=self._env(vault))
        self.assertEqual(shown.returncode, 0, shown.stderr)
        for want in (ledger, corrections, "record(s)", "policy:"):
            self.assertIn(want, shown.stdout, "data-show does not report %r" % want)

        out = os.path.join(vault, "export.json")
        exported = subprocess.run([sys.executable, self.TEL, "data-export", "--out", out],
                                  capture_output=True, text=True, env=self._env(vault))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertTrue(os.path.exists(out), "data-export wrote nothing: %s" % exported.stdout)
        bundle = json.loads(io.open(out).read())
        by_path = {f["path"]: f for f in bundle["files"]}
        self.assertIn("staging bucket", by_path[corrections]["content"],
                      "the export does not carry what is stored")
        out_mode = stat.S_IMODE(os.stat(out).st_mode)
        if _posix_modes_enforced():
            self.assertEqual(out_mode, 0o600, "the export is not owner-only")
        else:
            # The platform ignores the requested mode (Windows: mode lands
            # at 0o666 no matter what data-export asked for). The reachable
            # guarantees here: the writer requested owner-only regardless
            # (data_export's os.open call is unconditional, not
            # platform-branched -- read at tools/sbe_telemetry.py), and the
            # printed report names the mode it actually got rather than
            # claiming enforcement it cannot deliver (Fix 1).
            self.assertIn("owner-only intended", exported.stdout,
                         "data-export no longer names owner-only as the intent: %s"
                         % exported.stdout)
            self.assertIn("does not promise enforcement", exported.stdout,
                         "data-export's report no longer disclaims enforcement: %s"
                         % exported.stdout)
            self.assertIn("mode %03o" % out_mode, exported.stdout,
                         "data-export's reported mode does not match the file on disk: %s"
                         % exported.stdout)

        dry = subprocess.run([sys.executable, self.TEL, "data-purge"],
                             capture_output=True, text=True, env=self._env(vault))
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertTrue(os.path.exists(corrections),
                        "data-purge deleted without --yes")
        self.assertIn("--yes", dry.stdout)

        purged = subprocess.run([sys.executable, self.TEL, "data-purge", "--yes"],
                                capture_output=True, text=True, env=self._env(vault))
        self.assertEqual(purged.returncode, 0, purged.stderr)
        for path in (ledger, corrections):
            self.assertFalse(os.path.exists(path),
                             "data-purge reported success and %s is still on disk: %s"
                             % (path, purged.stdout))
            self.assertIn(path, purged.stdout, "%s was removed without being named" % path)
        self.assertIn("0 failed", purged.stdout)
        # The export made outside the vault survives, which is the point of
        # having an export at all, and is why the docs call it sensitive.
        self.assertTrue(os.path.exists(out))

    # -- THE DEFECT: `data-export --help` ran a real export --------------------
    #
    # The argv scanning for `--out` only ever matched "--out"; any other token,
    # including "-h" and "--help", fell through unconsumed and the command ran
    # for real. A mistyped flag was silently ignored the same way, so
    # `data-purge --catgory work --yes` (typo: catgory) purged every category
    # instead of refusing. Fixed by one shared check ahead of each command's own
    # parsing: -h/--help prints usage and exits 0 before anything is read or
    # written, and any other unrecognized flag refuses with usage and a nonzero
    # exit instead of running past it.

    def test_help_on_each_data_command_prints_usage_and_never_creates_the_vault(self):
        """The exact shape of the found defect: point BROTHERSBE_VAULT at a path
        that does not exist yet and ask for --help. Before the fix this ran a
        real data-export and wrote a bundle; the vault directory itself was
        never the thing at risk, but nothing under its parent should exist
        either, so the parent's own listing is hashed before and after."""
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, True)
        vault = os.path.join(parent, "never-created-vault")
        before = self._snapshot(parent)
        for subcmd, flag in (("data-show", "--help"), ("data-export", "--help"),
                             ("data-export", "-h"), ("data-purge", "--help"),
                             ("data-purge", "-h")):
            out = subprocess.run([sys.executable, self.TEL, subcmd, flag],
                                 capture_output=True, text=True, env=self._env(vault))
            self.assertEqual(out.returncode, 0, "%s %s: %s" % (subcmd, flag, out.stderr))
            self.assertIn("usage: %s" % subcmd, out.stdout,
                          "%s %s printed no usage: %r" % (subcmd, flag, out.stdout))
            self.assertFalse(os.path.exists(vault),
                             "%s %s created the vault it was only asked to describe"
                             % (subcmd, flag))
        after = self._snapshot(parent)
        self.assertEqual(before, after,
                         "--help changed something under %s; it must touch nothing" % parent)
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(),
                         "brothersbe-telemetry-export.json")),
                         "data-export --help wrote a real bundle into the cwd")

    def test_help_on_a_populated_vault_reports_and_changes_nothing_in_it(self):
        """Same defect, the other direction: a vault that already holds real
        data must come out of a --help call byte-for-byte and mtime-for-mtime
        identical, proven by hashing its listing rather than trusting that
        `data-export --help` merely says it wrote nothing."""
        vault = self._vault()
        stored = self._fire(vault, "outcomes-append", "/tmp/acme-backend",
                            BROTHERSBE_TELEMETRY_METRICS="1",
                            BROTHERSBE_TELEMETRY_CORRECTIONS="1")
        self.assertEqual(stored.returncode, 0, stored.stderr)
        before = self._snapshot(vault)
        for subcmd in ("data-show", "data-export", "data-purge"):
            out = subprocess.run([sys.executable, self.TEL, subcmd, "--help"],
                                 capture_output=True, text=True, env=self._env(vault))
            self.assertEqual(out.returncode, 0, "%s --help: %s" % (subcmd, out.stderr))
            self.assertIn("usage: %s" % subcmd, out.stdout)
        after = self._snapshot(vault)
        self.assertEqual(before, after,
                         "%s --help changed the populated vault" % subcmd)

    def test_an_unrecognized_flag_refuses_nonzero_instead_of_running_live(self):
        """The second half of the same defect: a flag the command does not
        know, most dangerously a typo of one it does (`--catgory` for
        `--category`), must never be silently ignored and run as if the
        operator had passed nothing. It refuses, names the bad flag, prints
        usage, and leaves a nonzero exit code for a caller to branch on."""
        vault = self._vault()
        stored = self._fire(vault, "outcomes-append", "/tmp/acme-backend",
                            BROTHERSBE_TELEMETRY_METRICS="1",
                            BROTHERSBE_TELEMETRY_CORRECTIONS="1")
        self.assertEqual(stored.returncode, 0, stored.stderr)
        before = self._snapshot(vault)
        cases = [("data-show", ["--bogus"]), ("data-export", ["--catgory"]),
                 ("data-purge", ["--catgory", "work", "--yes"])]
        for subcmd, bad_argv in cases:
            out = subprocess.run([sys.executable, self.TEL, subcmd] + bad_argv,
                                 capture_output=True, text=True, env=self._env(vault))
            self.assertNotEqual(out.returncode, 0,
                                "%s %r ran as if the flag were valid" % (subcmd, bad_argv))
            self.assertIn("usage: %s" % subcmd, out.stdout,
                          "%s %r refused without printing usage: %r"
                          % (subcmd, bad_argv, out.stdout))
            self.assertIn(bad_argv[0], out.stdout,
                          "%s %r did not name the flag it refused" % (subcmd, bad_argv))
        after = self._snapshot(vault)
        self.assertEqual(before, after,
                         "an unrecognized flag changed the vault before refusing")


class TestMarketplaceManifest(unittest.TestCase):
    """Wave 10 packages this plugin for `claude plugin marketplace add`, which
    means a SECOND file, `.claude-plugin/marketplace.json`, now carries the
    same version number `.claude-plugin/plugin.json` and `VERSION` already pin
    against each other (`TestPluginSurface.
    test_manifest_parses_and_agrees_with_the_version_file`). A marketplace
    entry that drifts from the plugin it names is the same silent packaging
    defect that test already guards against, one file further out, so this
    class extends the same pin rather than starting a second one.

    The shape asserted here was not taken from memory. The installed CLI
    (`claude --version` reported 2.1.207 when this was written) was asked
    directly, and its decisive lines were:

        `claude plugin marketplace add --help` prints:
        "Add a marketplace from a URL, path, or GitHub repo"

        `claude plugin validate --help` prints:
        "Validate a plugin or marketplace manifest"

    Neither --help prints a JSON schema, so the field shape itself (top-level
    name/owner/plugins, each plugin entry's name/source/description/version)
    was cross-checked against real, already-installed marketplace.json files
    on this machine that ship a single self-hosted plugin the same way this
    repository does (`~/.claude/plugins/marketplaces/mattpocock` and
    `.../karpathy-skills`, both using `"source": "./"` to name the repo's own
    root rather than a second clone URL), and then confirmed the only way
    that actually counts: running the installed `claude plugin validate`
    against the exact file this project ships, which the second test below
    re-runs so a future shape change is caught here rather than only at the
    next human's release-day run of the same command."""

    ROOT = os.path.join(HERE, "..")
    MANIFEST = os.path.join(ROOT, ".claude-plugin", "marketplace.json")

    def test_marketplace_manifest_parses_and_pins_every_version_together(self):
        self.assertTrue(os.path.exists(self.MANIFEST),
                        "%s does not exist; `claude plugin marketplace add` has nothing to read"
                        % self.MANIFEST)
        manifest = json.load(io.open(self.MANIFEST, encoding="utf-8"))
        self.assertEqual(manifest.get("name"), "brothersbe",
                         "the marketplace name; changing it changes what a user types after "
                         "`claude plugin marketplace add`")
        self.assertIn("owner", manifest, "marketplace.json has no owner")

        plugins = manifest.get("plugins")
        self.assertTrue(plugins, "marketplace.json declares no plugins")
        entry = plugins[0]
        self.assertEqual(entry.get("name"), "brothersbe",
                         "the plugin entry name must match .claude-plugin/plugin.json's own "
                         "name or `claude plugin install brothersbe@brothersbe` resolves to "
                         "the wrong thing")
        self.assertEqual(entry.get("source"), "./",
                         "this repository ships the plugin it also markets, so the entry "
                         "points at its own root, not a second clone URL")
        self.assertTrue(entry.get("description"), "the plugin entry has no description")

        version = io.open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
        plugin_manifest = json.load(io.open(
            os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8"))
        self.assertEqual(plugin_manifest.get("version"), version,
                         "plugin.json and VERSION already disagree; that is "
                         "TestPluginSurface's own pin, so if this line fails, that one is "
                         "failing too")
        self.assertEqual(entry.get("version"), version,
                         "marketplace.json's plugin entry says %s, VERSION says %s; `claude "
                         "plugin tag` validates that plugin.json and any enclosing marketplace "
                         "entry agree, so a release cut from this state fails that check"
                         % (entry.get("version"), version))
        top_version = (manifest.get("metadata") or {}).get("version")
        self.assertEqual(top_version, version,
                         "marketplace.json's own metadata.version says %s, VERSION says %s"
                         % (top_version, version))

    def test_marketplace_manifest_validates_against_the_installed_cli(self):
        claude_bin = shutil.which("claude")
        if not claude_bin:
            self.skipTest(
                "the `claude` CLI is not on PATH in this environment, so nothing ran "
                "`claude plugin validate` here. This is NO-DATA, not a pass: "
                "test_marketplace_manifest_parses_and_pins_every_version_together above still "
                "checks the shape by hand, but only a real run of the installed CLI proves the "
                "CLI itself accepts this file, and that did not happen on this run.")
        result = subprocess.run([claude_bin, "plugin", "validate", self.MANIFEST],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         "`claude plugin validate %s` exited %d:\nSTDOUT:\n%s\nSTDERR:\n%s"
                         % (self.MANIFEST, result.returncode, result.stdout, result.stderr))
        self.assertIn("Validation passed", result.stdout,
                      "the CLI exited 0 but did not say Validation passed: %s" % result.stdout)


class TestDesignOverrideLabelOnMissingArtifactsFail(unittest.TestCase):
    """Ledger 11: `check_artifacts` in tools/sbe_design.py builds `label`, the
    sentence naming the written tier, the computed tier, and the override's
    direction, and appends it on the PASS branch and on the NO-DATA (tier
    requires nothing) branch, but the FAIL-for-missing-artifacts branch, the
    one that fires right after an override moved the tier up and the newly
    required artifacts are not there yet, used to drop it: a reader saw only
    the missing list, never which tier was written or why. This pins the
    label back onto that FAIL line. Real subprocess against the real tool,
    matching TestDossierBindingScenario23's own discipline below."""

    DESIGN = os.path.join(HERE, "sbe_design.py")
    T1_ANSWERS = {"changes_contract": False, "crosses_boundary": True,
                  "reversible_under_hour": True, "touches_sensitive": False,
                  "consumers": "none"}
    PURPOSE = ("# Purpose\nProblem: x\nUsers: y\nSuccess: z\nNon-goals: w\nIf wrong: v\n")

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, rel, obj):
        path = os.path.join(self.dir, rel)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))

    def _run(self):
        out = subprocess.run([sys.executable, self.DESIGN, "artifacts", self.dir],
                             capture_output=True, text=True)
        return out.stdout + out.stderr

    def _verdict(self, text):
        # Three values, not two: a two-value (verdict, evidence) return is the
        # shape the honesty meta-test refuses outside a check registry, and
        # every multi-value helper in this project's test files is a 3-tuple
        # (see TestDossierBindingScenario23._verdict below, the sibling this
        # mirrors).
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "artifacts":
                return parts[1], line, text
        return None, "NO VERDICT LINE for 'artifacts' in:\n%s" % text, text

    def test_the_fail_for_missing_artifacts_after_an_override_names_the_label(self):
        # Written tier T3, answers that compute T1: a valid override (both
        # fields agree, reason well formed) that raises the tier. Only
        # 01-purpose.md exists, so T3's other six required artifacts are
        # missing and check_artifacts must FAIL, carrying the same override
        # label the PASS and NO-DATA branches print elsewhere in that
        # function.
        self._write("01-purpose.md", self.PURPOSE)
        self._write("00-intake.json", {"tier": "T3", "answers": self.T1_ANSWERS,
                                        "override": "T3",
                                        "override_reason": "the auditor requires the full dossier"})
        verdict, line, _ = self._verdict(self._run())
        self.assertEqual(verdict, "FAIL", line)
        self.assertIn("missing:", line)
        self.assertIn("declared override raising the tier to T3 from computed T1", line,
                      "the FAIL-for-missing-artifacts branch must carry the same override "
                      "label the PASS and NO-DATA branches print: %r" % line)
        self.assertIn("reason: the auditor requires the full dossier", line)


class TestDossierBindingScenario23(unittest.TestCase):
    """SCENARIO 23 (docs/BYPASS-COVERAGE.md row 23): a dossier from a finished
    change, left in place, reads identically to one written for the change in
    front of it, because nothing reads a commit. `00-intake.json` may now carry
    an OPTIONAL "binding": {"head": <commit>, "artifacts": {path: sha256}}.
    Every fixture here builds a REAL git repository and runs the real
    `tools/sbe_design.py` against it (matching how `tools/test_sbe_bypass.py`
    exercises the design checks), because a binding lives at the seam between
    a commit and a claim about it, and a mocked seam would test the mock.
    """

    DESIGN = os.path.join(HERE, "sbe_design.py")
    PURPOSE = ("# Purpose\nProblem: refunds settle late and support cannot say why.\n"
               "Users: the support desk and the finance close.\n"
               "Success: every refund reaches a terminal state within one business day.\n"
               "Non-goals: repricing, partial refunds.\nIf wrong: refunds stall silently.\n")
    ANSWERS = {"changes_contract": False, "crosses_boundary": True,
              "reversible_under_hour": True, "touches_sensitive": False, "consumers": "none"}

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "dana@example.invalid")
        self._git("config", "user.name", "Dana Author")
        self._git("config", "commit.gpgsign", "false")
        self._write("README.md", "base\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _git(self, *args):
        out = subprocess.run(["git"] + list(args), cwd=self.repo, capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError("git %s failed in %s: %s" % (" ".join(args), self.repo, out.stderr))
        return out.stdout.strip()

    def _write(self, rel, body):
        path = os.path.join(self.repo, rel)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def _sha256(self, rel):
        with open(os.path.join(self.repo, rel), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    #: T1 requires 08-behaviour.md as well as 01-purpose.md, so a fixture that
    #: writes only the purpose is INCOMPLETE and fails the artifacts check
    #: before these tests reach the binding behaviour they are actually about.
    #: The rows share vocabulary with PURPOSE above, because the artifacts
    #: check also requires an artifact to be about the same subject as its
    #: dossier, and they are the fixture's own rules rather than the shipped
    #: examples, which the behaviour check refuses.
    BEHAVIOUR = ("# Behaviour\n\n"
                 "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                 "|---|---|---|---|---|\n"
                 "| B1 | a refund the support desk can see | the refund is approved | "
                 "the refund reaches a terminal state within one business day | "
                 "settlement test asserts the terminal state inside the window |\n"
                 "| B2 | a refund still unsettled after one business day | the finance "
                 "close runs | the close names that refund rather than passing silently | "
                 "close-run test asserts the refund is named |\n")

    def _commit_dossier(self, message="add dossier"):
        """01-purpose.md and 08-behaviour.md plus an unbound 00-intake.json,
        committed. Returns the new commit's id, which is the "head this dossier
        was written against" every fixture below binds to."""
        self._write("01-purpose.md", self.PURPOSE)
        self._write("08-behaviour.md", self.BEHAVIOUR)
        self._write("00-intake.json", json.dumps({"tier": "T1", "answers": self.ANSWERS},
                                                 indent=2, sort_keys=True))
        self._git("add", "-A")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD")

    def _bind(self, binding):
        """Rewrites 00-intake.json to add `binding`, left UNCOMMITTED: a binding
        is the author's own claim about the tree in front of them, and nothing
        about resolving HEAD or re-hashing a covered file needs it committed."""
        self._write("00-intake.json", json.dumps({"tier": "T1", "answers": self.ANSWERS,
                                                  "binding": binding}, indent=2, sort_keys=True))

    def _run_design(self):
        out = subprocess.run([sys.executable, self.DESIGN, self.repo],
                             capture_output=True, text=True)
        return out.stdout + out.stderr

    def _verdict(self, text):
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses any
        # such function sitting outside a check registry.
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "artifacts":
                return parts[1], line, text
        return None, "NO VERDICT LINE for 'artifacts' in:\n%s" % text, text

    def test_bound_and_current_passes(self):
        head = self._commit_dossier()
        self._bind({"head": head, "artifacts": {"01-purpose.md": self._sha256("01-purpose.md")}})
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "PASS", line)
        self.assertIn(head[:12], line, "a verified binding should say which head it verified: %r" % line)

    def test_bound_then_head_moved_fails_by_name(self):
        head = self._commit_dossier()
        self._bind({"head": head, "artifacts": {"01-purpose.md": self._sha256("01-purpose.md")}})
        # A second, unrelated commit moves HEAD without touching the binding,
        # which is exactly scenario 23: the dossier now predates the commit in
        # front of it. The binding edit itself is never committed, matching
        # every fixture here, so committing "unrelated" is what advances HEAD.
        self._write("UNRELATED.md", "an unrelated later change\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "unrelated later change")
        new_head = self._git("rev-parse", "HEAD")
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "FAIL", line)
        self.assertIn(head[:12], line, "the stale bound head should be named: %r" % line)
        self.assertIn(new_head[:12], line, "the current head should be named: %r" % line)
        self.assertIn("re-bind deliberately", line)

    def test_artifact_digest_drift_fails_naming_the_file(self):
        head = self._commit_dossier()
        self._bind({"head": head, "artifacts": {"01-purpose.md": self._sha256("01-purpose.md")}})
        # Edited AFTER binding, and left uncommitted, so HEAD stays exactly
        # where the binding says it is: this isolates a digest mismatch from a
        # moved HEAD, which is the other, differently-named failure.
        self._write("01-purpose.md", self.PURPOSE + "Edited after binding, before re-binding.\n")
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "FAIL", line)
        self.assertIn("01-purpose.md", line)
        self.assertIn("changed since the dossier was bound", line)

    def test_absent_binding_is_unchanged_behavior(self):
        head = self._commit_dossier()
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "PASS", line)
        self.assertNotIn("bound", line.lower(),
                         "no binding was recorded, so nothing about one should appear: %r" % line)

    def test_an_unresolvable_bound_commit_is_no_data(self):
        self._commit_dossier()
        # 40 hex characters, never an object this repository has: no commit was
        # ever made with this id, and it is not the empty tree either.
        fake_head = "f" * 40
        self._bind({"head": fake_head, "artifacts": {"01-purpose.md": self._sha256("01-purpose.md")}})
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "NO-DATA", line)
        self.assertIn(fake_head[:12], line)
        self.assertNotEqual(verdict, "PASS", "an unresolvable binding must never pass: %r" % line)

    def test_a_binding_path_that_walks_out_of_the_repo_fails_by_name(self):
        # The outside file EXISTS and its recorded digest is TRUE, so the only
        # thing standing between this binding and a PASS is the containment
        # check itself; a missing-file refusal cannot satisfy this fixture,
        # which is what calibration demanded (with the check disabled, the
        # true digest verifies and the run PASSes, which is the red).
        outside = os.path.join(os.path.dirname(self.repo), "outside.md")
        with io.open(outside, "w", encoding="utf-8") as fh:
            fh.write("lives outside the repo on purpose\n")
        digest = hashlib.sha256(open(outside, "rb").read()).hexdigest()
        head = self._commit_dossier()
        self._bind({"head": head, "artifacts": {"../outside.md": digest}})
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "FAIL", line)
        self.assertIn("resolves outside", line)

    def test_an_unreadable_bound_artifact_fails_naming_the_file(self):
        head = self._commit_dossier()
        self._bind({"head": head, "artifacts": {"never-written.md": "0" * 64}})
        verdict, line, _ = self._verdict(self._run_design())
        self.assertEqual(verdict, "FAIL", line)
        self.assertIn("never-written.md", line)


class TestRenderSameNormalizesRawText(unittest.TestCase):
    """could_render_same and name_sets_could_collide were safe only because
    today's two call sites (tools/sbe_gate.py's approval check, reused by
    scripts/derive_refusal_table.py) hand them text already run through
    fold(), which composes via NFKC before either function ever compares a
    character. Called on RAW text, a composed-versus-decomposed spelling of
    the SAME rendered identity read as "proven different": a decomposed
    Hangul jamo run counts more characters than its precomposed syllable and
    trips the length check, and a precomposed accented letter missing from
    the curated confusable/Latin-name tables (Greek omega-with-tonos) reads
    as an unreadable ("opaque") letter that differs by code point from its
    own NFD form once _unmarked has stripped the mark back to plain omega.
    Both functions now normalize with plain_text (the same NFKC path
    fold() itself uses; no second normalizer introduced) at their own entry,
    so raw text now reads the way pre-folded text already did.

    Loads sbe_checks.py directly, the same pattern
    TestOneLineNeutralizesTheControlClass above uses, rather than importing
    it as a package, since this file is run as a script.
    """

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "sbe_checks_norm", os.path.join(HERE, "sbe_checks.py"))
        self.checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checks)

    def test_raw_nfc_nfd_accent_pair_reads_as_could_render_same(self):
        import unicodedata
        # U+038F GREEK CAPITAL LETTER OMEGA WITH TONOS: not ASCII, not East
        # Asian wide, and absent from both the curated confusable table and
        # the Latin-name fold (it is Greek, not Latin), so before the guard
        # it read as an "opaque" letter compared by code point against its
        # own NFD form (omega plus a combining tonos), which _unmarked
        # reduces to bare omega, a DIFFERENT code point: a proof of
        # difference for one letter compared against itself.
        nfc = "Ώ"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd, "the fixture must actually be two distinct spellings")
        self.assertTrue(self.checks.could_render_same(nfc, nfd),
                        "a precomposed letter and its own NFD decomposition, "
                        "passed raw, must not read as proven different")

    def test_raw_hangul_syllable_jamo_pair_reads_as_could_render_same(self):
        import unicodedata
        # Two precomposed Hangul syllables (2 characters) versus the same
        # text NFD-decomposed into its choseong/jungseong/jongseong jamo (6
        # characters, none of them a combining mark _unmarked would strip):
        # a bare length mismatch before the guard, though the two spellings
        # render identically.
        syllables = "안녕"       # 안녕
        jamo = unicodedata.normalize("NFD", syllables)
        self.assertNotEqual(len(syllables), len(jamo),
                            "the fixture must actually differ in raw character count")
        self.assertTrue(self.checks.could_render_same(syllables, jamo),
                        "a Hangul syllable spelling and its own jamo decomposition, "
                        "passed raw, must not read as proven different")

    def test_raw_nfc_nfd_accent_pair_collides_as_name_words(self):
        import unicodedata
        nfc = "Ώ"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertTrue(self.checks.name_sets_could_collide([nfc], [nfd]),
                        "one name word spelled NFC versus the same word spelled NFD "
                        "must not read as two different identities")

    def test_raw_hangul_syllable_jamo_pair_collides_as_name_words(self):
        import unicodedata
        syllables = "안녕"
        jamo = unicodedata.normalize("NFD", syllables)
        self.assertTrue(self.checks.name_sets_could_collide([syllables], [jamo]),
                        "one name word spelled with precomposed Hangul syllables versus "
                        "the same word spelled with decomposed jamo must not read as "
                        "two different identities")

    def test_genuinely_different_hangul_syllables_still_prove_different(self):
        """Control: the guard composes a raw jamo run back to ONE syllable,
        it does not make every Hangul comparison vacuously true. A DIFFERENT
        syllable, even compared against a raw jamo run, still differs by
        code point once both sides are one composed character each."""
        import unicodedata
        an = "안"                              # 안
        a_different_syllable = "녕"             # 녕
        an_as_jamo = unicodedata.normalize("NFD", an)
        self.assertFalse(self.checks.could_render_same(an_as_jamo, a_different_syllable),
                         "two genuinely different Hangul syllables must still "
                         "prove different, one of them passed as raw jamo")
        self.assertFalse(self.checks.name_sets_could_collide([an_as_jamo], [a_different_syllable]))


class TestReportIsAboutTheScannedTree(unittest.TestCase):
    """The split that opens the report groups checks by whether they opened a
    file inside the directory being reported on. That directory was read from
    the WORKING directory, not from the one the caller asked about, so running
    this tool from its own checkout against somebody else's tree inverted the
    whole report: the citation check, reading THIS repository's own docs, was
    filed under "these verdicts are about the code here", while the lint that
    had just read the caller's tree was filed under "not a statement about the
    code in this directory". The one line the reader came for sat beneath the
    heading that disowned it, which is the exact failure the split exists to
    prevent. Found 2026-07-31 while scanning three outside repositories."""

    def _report(self, target):
        r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_score.py"), target],
                           cwd=os.path.dirname(HERE),
                           env=dict(os.environ, BROTHERSBE_REGISTRIES=""),
                           capture_output=True, text=True)
        return r.stdout

    def test_the_heading_names_the_directory_the_caller_asked_about(self):
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "clean.py"), "w") as f:
                f.write("def f():\n    return 1\n")
            out = self._report(d)
            head = next((l for l in out.splitlines()
                         if l.startswith("CHECKS THAT OPENED A FILE IN")), "")
            self.assertIn(os.path.realpath(d), os.path.realpath(head.split(" IN ")[-1].split(" (")[0]),
                          "the report anchored on the working directory, not the "
                          "scanned one: %s" % head)

    def test_the_check_that_read_the_target_is_on_the_about_your_code_side(self):
        """The calibration that matters: the lint READ the caller's tree, so it
        must sit above the second heading, not below it."""
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "clean.py"), "w") as f:
                f.write("def f():\n    return 1\n")
            lines = self._report(d).splitlines()
            def index_of(pred):
                return next((i for i, l in enumerate(lines) if pred(l)), -1)
            first = index_of(lambda l: l.startswith("CHECKS THAT OPENED A FILE IN"))
            second = index_of(lambda l: l.startswith("CHECKS FED BY A VAULT OR REGISTRY"))
            lint = index_of(lambda l: l.startswith("silent-failure-lints"))
            self.assertNotEqual(-1, first, "no first heading in the report")
            self.assertNotEqual(-1, second, "no second heading in the report")
            self.assertNotEqual(-1, lint, "no lint line in the report")
            self.assertTrue(first < lint < second,
                            "the check that actually read the scanned tree was filed "
                            "under the heading saying it is NOT about that code "
                            "(first=%d lint=%d second=%d)" % (first, lint, second))


class TestVersionMark(unittest.TestCase):
    """The update notifier's state file must belong to exactly one tool.

    PARITY.md names the mechanisms this skill shares with BrotherModeUp, and the
    update notifier is one of them: both keep "which commit did the operator last
    see" in <vault>/99-System/telemetry. The vault path is the operator's own
    choice and nothing reserves it, so pointing BOTH tools at one vault is a
    supported setup. Under a shared basename each tool overwrites the other's
    stamp every session and both then report a version change forever, reading
    the sibling's commit hash as their own drift. Observed on a real machine
    2026-07-31, the day both vaults were pointed at one directory."""

    @staticmethod
    def _resolve_sibling():
        """Where BrotherModeUp telemetry can actually be read.

        The legacy skills path was the only candidate, so on a machine
        where the sibling ships as a plugin, or simply sits next door in
        this repository, this test skipped with a NO-DATA reason and the
        battery counted the whole green suite as NO-DATA (the 1.0.2 cut
        line, 2026-09-03). The in repo copy is preferred because it is
        the source this repository actually ships and it reads no
        operator home at all; the two install paths remain for a
        standalone checkout with no sibling product beside it.
        """
        candidates = [os.path.join(os.path.dirname(ROOT), "brothermode",
                                   "tools", "bm_telemetry.py")]
        candidates += sorted(glob.glob(os.path.expanduser(
            "~/.claude/plugins/cache/*/brothermode/*/tools/bm_telemetry.py")),
            reverse=True)
        candidates.append(os.path.expanduser(
            "~/.claude/skills/brothermode/tools/bm_telemetry.py"))
        for c in candidates:
            if os.path.isfile(c):
                return c
        return candidates[-1]

    SIBLING = _resolve_sibling.__func__()

    def test_the_marker_basename_is_owned_by_this_tool(self):
        base = os.path.basename(bm.VERSION_MARK)
        self.assertIn("brothersbe", base,
                      "the update marker %r does not name the tool that owns it, "
                      "so any sibling writing the same basename into a shared "
                      "vault silently overwrites it" % base)

    def test_the_version_marker_is_not_shared_with_the_sibling_skill(self):
        """Reads the sibling's real source when it is installed. When it is not,
        this reports that it examined nothing rather than passing: an absent
        sibling is no evidence that the two names differ."""
        if not os.path.isfile(self.SIBLING):
            self.skipTest("NO-DATA: the sibling BrotherModeUp is not installed at "
                          "%s, so its marker name could not be read and nothing "
                          "here was compared" % self.SIBLING)
        with io.open(self.SIBLING, encoding="utf-8", errors="replace") as f:
            sibling_src = f.read()
        m = re.search(r'VERSION_MARK\s*=\s*os\.path\.join\(\s*TEL_DIR\s*,\s*"([^"]+)"',
                      sibling_src)
        if not m:
            self.skipTest("NO-DATA: the sibling is installed but its VERSION_MARK "
                          "could not be read from %s, so the two names were not "
                          "compared" % self.SIBLING)
        self.assertNotEqual(os.path.basename(bm.VERSION_MARK), m.group(1),
                            "this tool and BrotherModeUp both write %r into "
                            "<vault>/99-System/telemetry; sharing one vault makes "
                            "each overwrite the other's stamp and both cry "
                            "'the skill changed' forever" % m.group(1))


class TestCheckUpdateFindsAWorktreeGitdir(unittest.TestCase):
    """check-update's git-dir resolution used to be os.path.isdir(SKILL_DIR/
    .git), true only for a normal clone. A LINKED WORKTREE's top-level .git
    is a FILE (`gitdir: <path>`), so that check was False and the whole
    command returned in total silence: exit 0, no output, no marker written,
    no warning. Reproduced against a real `git worktree add` checkout below,
    not a guess: every fixture here builds its own repo and vault in a temp
    dir rather than depending on any state of the machine running it. Skips
    (states NO-DATA) rather than passing when git is not on PATH, since git
    is what builds the fixture in the first place, not the thing under test."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("NO-DATA: git is not on PATH; the worktree fixture "
                          "this test builds itself could not be constructed, "
                          "so nothing about check-update was exercised")
        self._orig_skill_dir = bm.SKILL_DIR
        self._orig_tel_dir = bm.TEL_DIR
        self._orig_version_mark = bm.VERSION_MARK

    def tearDown(self):
        bm.SKILL_DIR = self._orig_skill_dir
        bm.TEL_DIR = self._orig_tel_dir
        bm.VERSION_MARK = self._orig_version_mark

    def _git(self, cwd, *args):
        r = subprocess.run(["git", "-C", cwd] + list(args),
                           capture_output=True, text=True)
        self.assertEqual(0, r.returncode,
                         "git %s failed building the fixture: %s" % (" ".join(args), r.stderr))
        return r

    def _run_check_update(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            bm.cmd_check_update()
            return sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

    def test_check_update_writes_the_marker_from_a_linked_worktree(self):
        with tempfile.TemporaryDirectory() as base:
            main = os.path.join(base, "main")
            os.makedirs(main)
            self._git(main, "init", "-q")
            self._git(main, "config", "user.email", "t@t.t")
            self._git(main, "config", "user.name", "t")
            io.open(os.path.join(main, "SKILL.md"), "w").write("v1\n")
            self._git(main, "add", "-A")
            self._git(main, "commit", "-qm", "init")
            linked = os.path.join(base, "linked")
            self._git(main, "worktree", "add", "-q", linked, "-b", "feature")
            # Confirm the fixture is really a linked worktree, not a clone:
            # its top-level .git must be a FILE, never a directory.
            self.assertTrue(os.path.isfile(os.path.join(linked, ".git")),
                            "fixture is not a linked worktree: .git is not a plain file")
            self.assertFalse(os.path.isdir(os.path.join(linked, ".git")))

            vault = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
            bm.SKILL_DIR = linked
            bm.TEL_DIR = os.path.join(vault, "99-System", "telemetry")
            bm.VERSION_MARK = os.path.join(bm.TEL_DIR, "installed-skill-version-brothersbe")
            self.assertFalse(os.path.exists(bm.VERSION_MARK), "marker existed before the run")

            out = self._run_check_update()
            self.assertTrue(os.path.isfile(bm.VERSION_MARK),
                            "check-update never reached the marker write from a linked "
                            "worktree; output was: %r" % out)
            marked = io.open(bm.VERSION_MARK).read().strip()
            self.assertRegex(marked, r"^[0-9a-f]{40}$",
                             "the written marker is not a commit sha: %r" % marked)

    def test_check_update_names_a_non_git_directory_instead_of_silence(self):
        with tempfile.TemporaryDirectory() as plain:
            vault = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
            bm.SKILL_DIR = plain
            bm.TEL_DIR = os.path.join(vault, "99-System", "telemetry")
            bm.VERSION_MARK = os.path.join(bm.TEL_DIR, "installed-skill-version-brothersbe")

            out = self._run_check_update()
            self.assertIn(plain, out,
                         "a genuinely non-git directory produced no named line: %r" % out)
            # The substance, not one phrasing of it: the line says the check was
            # skipped AND gives the reason it could not run. Asserting an exact
            # sentence made this fixture fail when the reason grew more specific,
            # which is a test guarding wording rather than behaviour.
            self.assertIn("skipped", out,
                          "the line does not say the check was skipped: %r" % out)
            self.assertIn(".git", out,
                          "the line does not say what it looked for: %r" % out)
            self.assertFalse(os.path.exists(bm.VERSION_MARK),
                             "a non-git directory should never write the version marker")


class TestHelpMapTemplate(unittest.TestCase):
    """Guards the help project map template shipped at
    skills/help/map-template.html: it must stay a real, substantial file, name
    each of the eleven double-brace slots exactly once (a repeated or missing
    slot breaks generation silently), stay free of any external script,
    stylesheet, image or url() reference (an anchor link is the one allowed
    exception, matching the explainer's own rule), and carry neither banned
    dash character.

    Calibrated by reinjecting an external stylesheet link into the template:
    the no-external-reference test went red, then the template was restored to
    its authored content, the restore verified by a byte-for-byte comparison
    against a temp copy taken before the reinjection. SKILL.md and this test
    file were confirmed byte-identical across that same excursion by their
    pre- and post-calibration `git hash-object`, proving the calibration
    touched only the template under test."""

    ROOT = os.path.abspath(os.path.join(HERE, ".."))
    TEMPLATE = os.path.join(ROOT, "skills", "help", "map-template.html")

    PLACEHOLDERS = ("PROJECT_NAME", "GENERATED_AT", "STAGE_SUMMARY", "STATUS_SECTIONS",
                    "PROCESS_DIAGRAM", "DATA_MODEL", "DECISIONS", "FILE_CLAIMS",
                    "NEXT_ACTION", "CODE_GUIDE", "MERMAID_JS")

    def _load(self):
        """Three values, not two: a two-value return reads as a possible
        (verdict, evidence) pair to the honesty meta-test, which refuses any
        such function sitting outside a check registry."""
        with io.open(self.TEMPLATE, encoding="utf-8") as handle:
            html = handle.read()
        size = os.path.getsize(self.TEMPLATE)
        return self.TEMPLATE, html, size

    def test_the_template_exists_and_is_over_3000_bytes(self):
        path, html, size = self._load()
        self.assertTrue(os.path.exists(path), "skills/help/map-template.html is missing")
        self.assertGreater(size, 3000,
                           "the map template is suspiciously thin: %d bytes" % size)

    def test_every_named_placeholder_appears_exactly_once(self):
        path, html, size = self._load()
        for name in self.PLACEHOLDERS:
            token = "{{%s}}" % name
            count = html.count(token)
            self.assertEqual(count, 1,
                             "%s must appear exactly once in the template, found %d "
                             "(a generator cannot fill a slot that is missing or "
                             "duplicated)" % (token, count))

    def test_no_external_script_stylesheet_image_or_url_reference(self):
        path, html, size = self._load()
        tag_pattern = re.compile(r"<(script|img|link|source)\b([^>]*)>", re.IGNORECASE)
        attr_pattern = re.compile(r"(src|href)\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
        offenders = []
        for tag_match in tag_pattern.finditer(html):
            tag_name = tag_match.group(1)
            for attr_name, value in attr_pattern.findall(tag_match.group(2)):
                if value.lower().startswith("http://") or value.lower().startswith("https://"):
                    offenders.append("<%s %s=%s>" % (tag_name, attr_name, value))
        self.assertEqual(offenders, [],
                         "external script/img/link/source reference(s) found: %s"
                         % offenders)
        css_import = re.search(r"@import\s+[\"']?https?://", html, re.IGNORECASE)
        css_url = re.search(r"url\(\s*[\"']?https?://", html, re.IGNORECASE)
        self.assertIsNone(css_import, "an @import pulls from an external http(s) URL")
        self.assertIsNone(css_url, "a CSS url() points at an external http(s) URL")

    def test_zero_banned_dash_characters(self):
        path, html, size = self._load()
        em_dash_count = html.count(chr(0x2014))
        en_dash_count = html.count(chr(0x2013))
        self.assertEqual(em_dash_count, 0, "an em dash (U+2014) slipped into the map template")
        self.assertEqual(en_dash_count, 0, "an en dash (U+2013) slipped into the map template")


class TestProfileInvariantsOnTheMergePath(unittest.TestCase):
    """The profile's four invariants, run from a suite CI already runs.

    The full battery (defect re-injected, verdict watched go red, file restored,
    verdict watched go green again) is tools/test_sbe_profile.py, which the shipped
    workflow does not yet call. This class is the one assertion that rides an
    existing step: the shipped tree's own profile must be clean under --strict, so a
    module leaking into the default profile, or a law falling out of the routing
    table, stops a merge today rather than on the day a workflow step is added.

    Every check name below is written out BY HAND rather than derived from
    sbe_profile.CHECKS, and the count is asserted from the report's own summary
    line. A hostile refuter deleted `check_module_enforcement` from that tuple and
    this class stayed green, because it named only three of the four checks: a check
    that can be removed with no test noticing is not on the merge path, whatever the
    report prints. Deriving the expected names from the tuple would recreate exactly
    that hole, since the deletion would also shrink the expectation. Adding a fifth
    check therefore means editing this list, on purpose, which is the point.
    """

    def test_the_shipped_tree_passes_its_own_profile_checks_under_strict(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        r = subprocess.run([sys.executable, os.path.join(HERE, "sbe_profile.py"),
                            "check", "--root", root, "--strict"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(0, r.returncode,
                         "tools/sbe_profile.py check --strict blocks on this tree:\n%s%s"
                         % (r.stdout, r.stderr))
        for name in ("profile-declaration", "profile-law-routing", "profile-module-isolation",
                     "profile-module-enforcement"):
            self.assertIn(name, r.stdout, "the profile report lost the %s check" % name)
        self.assertIn("4 check(s)", r.stdout,
                      "the profile report no longer runs the four checks this class pins; "
                      "a check was added or removed without this list being updated:\n%s"
                      % r.stdout)
        red = [ln for ln in r.stdout.splitlines()
               if len(ln.split(None, 2)) == 3 and ln.split(None, 2)[1] == "FAIL"]
        self.assertEqual([], red, "a profile check is red: %s" % red)
        self.assertIn("19 law(s) and 6 phase(s)", r.stdout,
                      "every law and phase must still resolve from the routing table")


class TestTheUpdateNoticeIsUnconditional(unittest.TestCase):
    """The notice that the skill itself changed prints at EVERY profile.

    The regression this blocks, in one sentence: a size optimisation put the
    session-start update check behind the release module, and the observable effect
    was that an already-installed copy, upgraded, silently stopped printing

        BROTHERSBE: the skill changed since your last session (<old> -> <new>).
        Read the diff before relying on it:
          git -C <install> log --oneline <old>..<new>

    A user who is not told the governance engine changed will trust an install they
    have not read, so this is a trust regression rather than a size win, and it was
    refused. The decision and the 236 bytes it costs are written down in
    references/modules.md and references/module-release.md.

    This class lives in tools/test_sbe.py, which CI runs, rather than in
    tools/test_sbe_profile.py, which it does not: a control for a safety-adjacent
    notice belongs on the merge path. Both halves matter. The behavioural half runs
    the real hook and reads what it actually printed. The source half pins the
    absence of a guard, so re-introducing one is red even on a machine where the
    behavioural half could not reach a git checkout.
    """

    NOTICE = "BROTHERSBE: the skill changed since your last session"

    def test_the_default_profile_still_prints_the_skill_changed_notice(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        work = tempfile.mkdtemp()
        try:
            # A FRESH vault whose marker holds a different sha: check-update writes
            # that marker itself, so a vault it has already seen prints nothing and
            # the assertion below would pass over silence.
            vault = os.path.join(work, "vault")
            tel = os.path.join(vault, "99-System", "telemetry")
            os.makedirs(tel)
            with io.open(os.path.join(tel, "installed-skill-version-brothersbe"),
                         "w", encoding="utf-8") as fh:
                fh.write("0" * 40 + "\n")
            env = dict(os.environ, BROTHERSBE_VAULT=vault)
            env.pop("SBE_PROFILE", None)          # the DEFAULT profile, which is the case
            env.pop("SBE_PROFILE_MODULES", None)  # the gated draft got wrong
            r = subprocess.run([sys.executable,
                                os.path.join(root, "tools", "sbe_sessionstart.py")],
                               input="{}", capture_output=True, text=True, env=env,
                               timeout=180)
            self.assertEqual(0, r.returncode, "SessionStart must always exit 0")
            if "the update check is skipped" in r.stdout:
                self.skipTest("not a git checkout the update check can read, so the "
                              "notice could not fire here; the source half below still runs")
            self.assertIn(self.NOTICE, r.stdout,
                          "the default profile lost the notice that the skill changed "
                          "under the user:\n%s" % r.stdout)
            self.assertTrue(any(" log --oneline " in ln for ln in r.stdout.splitlines()),
                            "the notice printed without the git command that reads the "
                            "diff, which is the half a user acts on:\n%s" % r.stdout)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_no_profile_guard_stands_between_the_hook_and_the_update_check(self):
        """The source half: check-update must not sit inside an OPEN `_gate_open`
        (`enabled <id>`) guard. Block extent is tracked by indentation rather than
        a fixed window of lines above, because the telemetry guard's suite ends
        two lines before check-update and a window would read that closed guard
        as if it applied."""
        with io.open(os.path.join(HERE, "sbe_sessionstart.py"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        guards, offenders, found = [], [], False
        for i, raw in enumerate(lines):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip())
            while guards and indent <= guards[-1][0]:
                guards.pop()
            if "check-update" in line:
                found = True
                if guards:
                    offenders.append((i + 1, [g[1] for g in guards]))
            if line.startswith("if ") and "_gate_open(" in line:
                guards.append((indent, (i + 1, line)))
        self.assertTrue(found, "the hook no longer runs check-update at all")
        self.assertEqual([], offenders,
                         "check-update is gated again: %s. The skill-changed notice must "
                         "print at every profile, including the default one." % offenders)
        spec_p = importlib.util.spec_from_file_location(
            "sbe_profile_pin", os.path.join(HERE, "sbe_profile.py"))
        prof = importlib.util.module_from_spec(spec_p)
        spec_p.loader.exec_module(prof)
        self.assertNotIn("check-update", prof.STARTUP_EMITTERS,
                         "sbe_profile.py claims a module owns check-update, which would "
                         "make an ungated hook FAIL module-isolation and push the notice "
                         "back behind a profile: %s" % (prof.STARTUP_EMITTERS,))


class TestEveryShippedLinkResolves(unittest.TestCase):
    """A link a reader clicks must land somewhere.

    Found by walking the install-and-first-run journey as a stranger rather
    than by reading: docs/for-engineers/00-READ-ME-FIRST.md offered "the Field
    Book" at a filename that has not existed since the booklet was renamed.
    Nothing caught it, because the doc-truth evals compare pasted OUTPUT
    against tools and say nothing about navigation. This closes the class: a
    relative link in any shipped document either resolves on disk or this
    fails and names it.

    Scope stated rather than implied: relative links only. External URLs are
    the citation inventory's job, and anchors within a page are not checked
    because nothing here parses headings."""

    SKIP_DIRS = ("/superpowers/", "/handover-")

    def _shipped_docs(self):
        import glob as _glob
        out = [os.path.join(ROOT, "README.md")]
        for p in _glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True):
            if any(marker in p for marker in self.SKIP_DIRS):
                continue
            out.append(p)
        return [p for p in out if os.path.isfile(p)]

    def test_no_shipped_document_offers_a_link_that_lands_nowhere(self):
        import re as _re
        docs = self._shipped_docs()
        self.assertGreater(len(docs), 20,
                           "the sweep found almost no documents, which means it is "
                           "scanning the wrong place and its silence would prove nothing")
        broken, checked = [], 0
        for path in docs:
            with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            for m in _re.finditer(r"\]\(([^)#:]+?)(?:#[^)]*)?\)", body):
                target = m.group(1).strip()
                if not target or target.startswith(("http", "mailto:", "<")):
                    continue
                checked += 1
                full = os.path.normpath(os.path.join(os.path.dirname(path), target))
                if not os.path.exists(full):
                    broken.append("%s -> %s" % (os.path.relpath(path, ROOT), target))
        self.assertGreater(checked, 50,
                           "fewer relative links than expected were examined (%d), so a "
                           "clean result here would not mean the documents are sound"
                           % checked)
        self.assertEqual(broken, [],
                         "%d shipped link(s) resolve to nothing: %s"
                         % (len(broken), "; ".join(broken[:10])))



class TestEverySuiteIsWiredIntoAGate(unittest.TestCase):
    """Every suite runs in BOTH gates, and the two gates agree.

    History, because it is the reason this is shaped the way it is. On
    2026-08-25 this class counted suites NAMED in `.github/workflows/*.yml` and
    froze a list of 39 that were named in none. Two things were wrong with that
    and both were found the next morning.

    First, it measured the weaker gate. The workflow is `on: workflow_dispatch:`
    only and had not run since 2026-08-15; its own header says the coverage went
    to "the local runner, which executes this same battery on a real Mac every
    run". The battery is what gates every release. A suite wired only into the
    workflow is wired into the gate that fires less often.

    Second, it counted NAMES. A suite mentioned in a comment is not a suite that
    runs, and this repository has exactly one such case, which is why a name
    count and an invocation count differ by one here.

    So this now asserts three things, in the order a reader needs them:
    every suite is INVOKED by the battery, every suite is INVOKED by the
    workflow, and the two gates invoke the SAME set. That last one is what the
    old shape could not see: the drift ran in both directions at once, 39 suites
    in neither gate and 10 more the workflow ran while the battery did not.

    `test_sbe_prverify_live.py` is the one deliberate exception and is in
    neither. It needs SBE_LIVE_GH_REPO, SBE_LIVE_GH_PR and a discoverable
    token, and prints one NO-DATA line and exits 0 without them, so wiring it
    would either skip silently on every run or force a token into this estate
    as a CI secret. That reasoning is `docs/KNOWN-LIMITS.md`'s, not this test's.
    """

    DELIBERATELY_UNWIRED = ("test_sbe_prverify_live.py",)

    WORKFLOWS = os.path.join(".github", "workflows")
    BATTERY = os.path.join("release-control", "baseline", "run-battery.sh")

    def _read(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        suites = sorted(os.path.basename(p) for p in
                        glob.glob(os.path.join(root, "tools", "test_sbe_*.py")))
        self.assertTrue(
            suites,
            "no tools/test_sbe_*.py files were found at all, which is NO-DATA "
            "rather than a pass: this repository has dozens, so an empty "
            "result means this check looked in the wrong place")

        wf_files = sorted(glob.glob(os.path.join(root, self.WORKFLOWS, "*.yml")))
        if not wf_files:
            # The public export deliberately does not carry this product's CI
            # wiring (docs/plan/EXPORT-ALLOWLIST.txt, the M6 note: "CI wiring
            # not needed to install or run the plugin"), so in a public clone
            # there is nothing to read. That is NO-DATA and it says so by
            # name, rather than a FAIL that reports a catastrophe which is
            # really a directory this tree was never meant to carry. In the
            # hub, where the gate matters, the files are present and every
            # assertion below still runs.
            self.skipTest(
                "NO-DATA: no workflow files under %s, so neither gate's "
                "invocation set could be read here. A clone that does not "
                "carry the CI wiring cannot answer this question, and an "
                "empty read is not a pass" % self.WORKFLOWS)
        wf = "\n".join(io.open(f, encoding="utf-8", errors="replace").read()
                       for f in wf_files)

        bat_path = os.path.join(root, self.BATTERY)
        if not os.path.isfile(bat_path):
            # Same boundary as the workflows above: release-control/ is the
            # internal release kit and the public export does not carry it.
            self.skipTest(
                "NO-DATA: no battery at %s, so the battery's invocation set "
                "could not be read here. This clone does not carry the "
                "release-control kit, and an empty read is not a pass"
                % self.BATTERY)
        bat = io.open(bat_path, encoding="utf-8", errors="replace").read()

        # INVOKED, never merely mentioned. A name inside a comment is not a run,
        # and this repository contains exactly that case on purpose.
        wf_run = set(re.findall(
            r"run:\s*python3?\s+tools/(test_[a-z_0-9]+\.py)", wf))
        bat_run = set(re.findall(
            r"^run_step\s+\S+\s+python3?\s+tools/(test_[a-z_0-9]+\.py)",
            bat, re.M))
        self.assertTrue(
            wf_run and bat_run,
            "one of the two gates invokes no suite at all (workflow %d, battery "
            "%d), which is NO-DATA rather than a pass: the invocation patterns "
            "have probably stopped matching the files' real shape, and every "
            "suite would look unwired for a reason that is about this test"
            % (len(wf_run), len(bat_run)))
        return suites, wf_run, bat_run

    def test_every_suite_is_invoked_by_the_battery(self):
        suites, _wf, bat_run = self._read()
        missing = [n for n in suites
                   if n not in bat_run and n not in self.DELIBERATELY_UNWIRED]
        self.assertEqual(
            missing, [],
            "%d suite(s) are not invoked by %s, which is the gate that runs on "
            "every release: %s. A suite that exists but never runs is not a "
            "control. Add a run_step line, or if it genuinely cannot run there, "
            "add it to DELIBERATELY_UNWIRED with the reason recorded in "
            "docs/KNOWN-LIMITS.md rather than silently."
            % (len(missing), self.BATTERY, missing))

    def test_every_suite_is_invoked_by_the_workflow(self):
        suites, wf_run, _bat = self._read()
        missing = [n for n in suites
                   if n not in wf_run and n not in self.DELIBERATELY_UNWIRED]
        self.assertEqual(
            missing, [],
            "%d suite(s) are not invoked by any workflow: %s. The workflow is "
            "dispatch-only and fires less often than the battery, but a suite "
            "absent from it is invisible the day somebody dispatches one."
            % (len(missing), missing))

    def test_the_two_gates_invoke_the_same_set(self):
        _suites, wf_run, bat_run = self._read()
        only_wf = sorted(wf_run - bat_run)
        only_bat = sorted(bat_run - wf_run)
        self.assertEqual(
            (only_wf, only_bat), ([], []),
            "the two gates have drifted apart.\n"
            "  invoked by the workflow but not the battery: %s\n"
            "  invoked by the battery but not the workflow: %s\n"
            "This is the assertion the earlier version of this test could not "
            "make, because it looked at one gate only. When it was finally "
            "asked, the drift turned out to run BOTH ways at once: 39 suites in "
            "neither and 10 the workflow ran while the battery did not."
            % (only_wf or "none", only_bat or "none"))

    # One-repo transition (M3/M4): this product lives inside the hub
    # repository, which ships its OWN battery, scripts/check_all.sh, above
    # BOTH gates this class already holds to each other. That hub battery ran
    # evals/run_evals.py and this file directly but never release-control/
    # baseline/run-battery.sh, the ninety-plus-suite battery the two tests
    # above hold in step with the workflow, so a hub run answered for two
    # suites out of ninety while believing it had answered for the product.
    # Registered the same change (QA reviewer finding 3, 2026-09-04) that
    # lands the hub line calling this script; the smallest addition that
    # fails the moment someone removes that line rather than only when the
    # workflow and this battery drift from each other.
    HUB_BATTERY = os.path.join("scripts", "check_all.sh")

    def test_the_hub_battery_also_registers_this_script(self):
        hub_root = os.path.dirname(os.path.dirname(os.path.abspath(ROOT)))
        hub_battery = os.path.join(hub_root, self.HUB_BATTERY)
        if not os.path.isfile(hub_battery):
            self.skipTest(
                "NO-DATA: no %s found two directories above this product "
                "(%s); this checkout does not have the product nested "
                "inside the hub repository, so there is no outer battery "
                "to check" % (self.HUB_BATTERY, hub_root))
        text = io.open(hub_battery, encoding="utf-8", errors="replace").read()
        rel = self.BATTERY.replace(os.sep, "/")
        # INVOKED, never merely mentioned, matching _read()'s own rule above:
        # a bare assertIn against the whole file passed this test the first
        # time it was written, because the path also appears in this test's
        # own explanatory comment a few lines up. Require the path to appear
        # on a line that actually starts a run_check call.
        invoked = re.search(
            r'^\s*run_check\s+"[^"]+"\s+.*' + re.escape(rel), text, re.M)
        self.assertTrue(
            invoked,
            "%s does not INVOKE %s on any run_check line (the path may still "
            "appear in a comment, which does not run it). Add a run_check "
            "line there that runs this product's own battery from the "
            "product directory (for example: run_check "
            "\"product-brothersbe-battery\" sh -c 'cd products/brothersbe "
            "&& sh %s ...'), or the ninety-plus suites it wires stay "
            "invisible to the hub's own battery run."
            % (self.HUB_BATTERY, rel, rel))


class TestTheWaiverBlastRadiusStaysOneRule(unittest.TestCase):
    """The self-minted-waiver route from BLOCKED to PASS must not widen.

    Recorded in `docs/KNOWN-LIMITS.md` under the protected-evidence entry, and
    demonstrated by running it: an exception file that asserts its own
    `approval.protected: true` and names itself as approver is ACCEPTED, because
    `waiver_defects` checks the shape of that claim and nothing can check its
    provenance in this release. On its own that only downgrades a requirement to
    WAIVED, which still reads "never passed".

    The overall verdict flips only when a SECOND ingredient is present: a rule
    declaring `strictWaivers: false`, on which a waived requirement never joins
    the blocking set. Exactly one rule does, `api-or-event-contract`. That
    single fact is the containment, and it is the kind of thing a well-meaning
    edit widens without noticing, since setting one more rule non-strict looks
    locally reasonable and produces no red.

    So this pins the blast radius rather than the hole. The hole closes with
    attestation binding an approval to an identity, which is the founder's
    change and not a session's. Until then, if a second rule goes non-strict,
    this fails and says what it means.

    It lives in `tools/test_sbe.py` rather than beside the other policy tests
    for a reason found the same night: `tools/test_sbe_policy.py` is one of the
    39 suites no workflow runs, so a pin placed there would not execute. See
    TestEverySuiteIsWiredIntoAGate above.
    """

    def _rules(self):
        from brothersbe.program import load_yaml_file
        root = os.path.abspath(os.path.join(HERE, ".."))
        path = os.path.join(root, ".sbe", "policy.yml")
        self.assertTrue(
            os.path.isfile(path),
            "no .sbe/policy.yml at %s, which is NO-DATA rather than a pass: "
            "this repository ships one, so an absent file means this check "
            "looked in the wrong place" % path)
        data = load_yaml_file(path)
        rules = (data or {}).get("rules")
        self.assertTrue(
            rules,
            "the policy parsed but declares no rules, which is NO-DATA rather "
            "than a pass: a policy with no rules would make every waiver "
            "question vacuous and this test would report safety it never "
            "measured")
        return rules

    def test_exactly_one_rule_lets_an_accepted_waiver_avoid_blocking(self):
        rules = self._rules()
        for r in rules:
            self.assertIn(
                "strictWaivers", r,
                "rule %r declares no strictWaivers, so whether an accepted "
                "exception blocks on it cannot be read from the policy at all"
                % r.get("id"))
        lenient = sorted(r["id"] for r in rules if r["strictWaivers"] is False)
        self.assertEqual(
            lenient, ["api-or-event-contract"],
            "the set of rules where an ACCEPTED exception does not block has "
            "changed, and that set is the blast radius of a hole this release "
            "cannot close: an exception asserts its own protection and nothing "
            "verifies it, so every non-strict rule is a route from BLOCKED to "
            "PASS that a local process can mint for itself. Found: %s. If a "
            "rule was deliberately made non-strict, widen this assertion and "
            "say why in docs/KNOWN-LIMITS.md next to the protected-evidence "
            "entry. Do not widen it silently." % (lenient,))

    def test_an_exception_missing_any_of_its_four_parts_is_still_refused(self):
        sys.path.insert(0, os.path.join(os.path.abspath(os.path.join(HERE, "..")), "src"))
        try:
            from brothersbe.policy import waiver_defects
        finally:
            sys.path.pop(0)
        import datetime
        today = datetime.date(2026, 1, 1)
        complete = {"reason": "a stated reason", "owner": "a named owner",
                    "expiry": "2099-01-01",
                    "approval": {"protected": True, "approver": "a named approver"}}
        self.assertEqual(
            waiver_defects(dict(complete), today), [],
            "a complete exception is refused, so this test can no longer tell "
            "the difference between the four parts being required and "
            "everything being rejected")
        for field in ("reason", "owner", "expiry", "approval"):
            broken = dict(complete)
            broken.pop(field)
            self.assertTrue(
                waiver_defects(broken, today),
                "an exception missing %r was accepted. Scenario B8 of "
                "TEST-PROTOCOL.md requires that three of four is not an "
                "exception, it is a note, and a note does not clear a control"
                % field)
        expired = dict(complete)
        expired["expiry"] = "2020-01-01"
        self.assertTrue(
            waiver_defects(expired, today),
            "an exception whose expiry has passed was accepted; an expired "
            "exception waives nothing")
        unprotected = dict(complete)
        unprotected["approval"] = {"protected": False, "approver": "a named approver"}
        self.assertTrue(
            waiver_defects(unprotected, today),
            "an exception whose approval is not marked protected was accepted")


class TestTheReleaseDocNamesEveryStepTheToolPrints(unittest.TestCase):
    """Two release checklists existed and only one of them learned.

    `src/brothersbe/versionbump.py` prints REMINDERS after a version bump: the
    regenerations a releaser still owes, each of which writes TRACKED files.
    `docs/RELEASE.md` prints its own numbered list for a human. On 2026-08-25
    three of the tool's five steps were absent from the document:
    `evals/replay_book.py --write`, `sbe book` and
    `tools/regen_sandbox_guide.py`. The document also placed the manifest at
    step 3, BEFORE the regenerations it did not mention, so a releaser
    following it would hash the tree and then change it.

    The `sbe book` line was added to the tool on 2026-08-24 because the 3.4.1
    battery failed at command 21 of 52 over exactly this. The release commit
    that added it wrote down the general lesson: A CHECKLIST THAT IS THE ONLY
    PLACE A RELEASER IS TOLD WHAT REMAINS IS ONLY AS COMPLETE AS THE LAST
    FAILURE THAT TAUGHT IT. What neither noticed is that there were two
    checklists, so the lesson landed in one and the other kept its old shape.

    This asserts the containment: every step the tool prints must be NAMED in
    the document. It deliberately does not compare order or wording, because
    the document is prose and the tool is a list, and a test demanding they
    match verbatim would be false precision that gets switched off. Naming is
    the property that matters: a releaser reading the document must not be able
    to finish it having never heard of a step the tool considers owed.
    """

    def test_every_reminder_the_tool_prints_is_named_in_the_release_doc(self):
        root = os.path.abspath(os.path.join(HERE, ".."))
        sys.path.insert(0, os.path.join(root, "src"))
        try:
            from brothersbe.versionbump import REMINDERS
        finally:
            sys.path.pop(0)
        self.assertTrue(
            REMINDERS,
            "versionbump prints no reminders at all, which is NO-DATA rather "
            "than a pass: with an empty list every document trivially names "
            "every step and this test would measure nothing")

        doc_path = os.path.join(root, "docs", "RELEASE.md")
        self.assertTrue(
            os.path.isfile(doc_path),
            "no docs/RELEASE.md at %s, which is NO-DATA rather than a pass" % doc_path)
        doc = io.open(doc_path, encoding="utf-8", errors="replace").read()

        missing = []
        for reminder in REMINDERS:
            # The command is the part before the colon, which is what a reader
            # would search for. Comparing the whole reminder would compare
            # prose against prose and fail on a reworded parenthetical.
            command = reminder.split(":", 1)[0].strip()
            if command not in doc:
                missing.append(command)
        self.assertEqual(
            missing, [],
            "docs/RELEASE.md never names %d step(s) that `sbe version bump` "
            "prints as still owed: %s. Each of those writes tracked files, so "
            "a releaser who follows the document instead of the tool ships a "
            "stale generated artifact and a manifest that hashed the tree "
            "before it finished changing. Add them to the document, or if a "
            "step genuinely does not belong in the human list, say so there "
            "in words rather than omitting it."
            % (len(missing), missing))


class TestTheOwnerOnlyWriteSitesStayNamed(unittest.TestCase):
    """docs/KNOWN-LIMITS.md names four owner-only write sites; they must exist.

    That entry described the Windows limitation accurately and cited its four
    sites by LINE NUMBER. By 2026-08-25 every one of the four pointed at
    unrelated code, because the file had grown underneath the prose and prose
    has no gate. The citation is now by function name, which survives edits
    above it, and this test is what keeps the four honest: if one stops making
    an owner-only write, or is renamed, or is deleted, the entry has become
    fiction and this fails saying which.

    It asserts presence, not correctness. Whether 0o600 means anything on a
    given platform is the whole subject of the entry and is NOT what this
    measures.
    """

    SITES = ("scan_corrections", "atomic_append_text", "_write_brief", "cmd_data_export")

    def test_each_named_site_still_makes_an_owner_only_write(self):
        import re
        root = os.path.abspath(os.path.join(HERE, ".."))
        path = os.path.join(root, "tools", "sbe_telemetry.py")
        self.assertTrue(
            os.path.isfile(path),
            "no tools/sbe_telemetry.py at %s, which is NO-DATA rather than a "
            "pass: the entry this guards is about that file" % path)
        lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()

        owner = 0
        current, found = None, {}
        for line in lines:
            m = re.match(r"\s*def (\w+)", line)
            if m:
                current = m.group(1)
            if "0o600" in line:
                owner += 1
                if current:
                    found.setdefault(current, 0)
                    found[current] += 1
        self.assertTrue(
            owner,
            "no 0o600 write appears anywhere in sbe_telemetry.py, which is "
            "NO-DATA rather than a pass: with none, every named site would "
            "look missing and this would report a rewrite that did not happen")

        missing = [s for s in self.SITES if s not in found]
        self.assertEqual(
            missing, [],
            "docs/KNOWN-LIMITS.md names %s as the owner-only write sites in "
            "sbe_telemetry.py. %s no longer make one, so that entry now cites "
            "code that is not there. Update the entry and this list together, "
            "and note the entry was already wrong once this way: it cited line "
            "numbers until they all pointed at unrelated code."
            % (", ".join(self.SITES), missing))


# This block stays at the END of the file, and that placement is load bearing
# rather than stylistic. `unittest.main()` runs at the point it appears, so any
# class defined BELOW it is never collected when this file is run directly as
# `python3 tools/test_sbe.py`, which is exactly how the battery and CI run it.
# Three classes were appended below it on 2026-08-25 and silently did not run:
# the count stayed at 137 when it should have reached 140, which is the only
# reason it was caught. They passed under `python3 -m unittest`, because that
# imports the module without executing this block, so every direct check of
# them looked green.
#
# That is the third instance of one shape in a single night, after 39 suites
# wired into no workflow and a release document missing three steps the tool
# prints: a control that exists, passes when you ask it directly, and is never
# asked by the thing that gates. Append new classes ABOVE this line.
class TestSbeRepoScope(unittest.TestCase):
    """E76: per-repository hook scoping. tools/sbe_repo_scope.py is the
    shared reader every hook calls at entry; this proves its own logic
    (find_repo_root, the config parse, the once-per-session notice) and
    then drives EVERY hook command hooks/hooks.json actually registers, as
    real subprocesses, so a hook added later is covered automatically
    rather than by a hand-picked sample."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.tmp], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_config(self, text):
        d = os.path.join(self.tmp, ".brother")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "config"), "w", encoding="utf-8") as f:
            f.write(text)

    def _load(self):
        spec = importlib.util.spec_from_file_location(
            "sbe_repo_scope_under_test", os.path.join(HERE, "sbe_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_absent_config_is_active(self):
        mod = self._load()
        self.assertFalse(mod.hooks_off(cwd=self.tmp))

    def test_off_line_turns_hooks_off(self):
        self._write_config("hooks: off\n")
        mod = self._load()
        self.assertTrue(mod.hooks_off(payload={"session_id": "s1"}, cwd=self.tmp))

    def test_off_line_is_case_insensitive_past_comments_and_blanks(self):
        self._write_config("# a comment\n\nHOOKS: OFF\n")
        mod = self._load()
        self.assertTrue(mod.hooks_off(cwd=self.tmp))

    def test_readable_text_that_is_not_the_off_line_stays_active(self):
        self._write_config("this is not the magic line\n")
        mod = self._load()
        with mock.patch("sys.stderr", io.StringIO()):
            off = mod.hooks_off(cwd=self.tmp)
        self.assertFalse(off)

    def test_undecodable_bytes_default_active_with_one_diagnostic_line(self):
        d = os.path.join(self.tmp, ".brother")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config"), "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        mod = self._load()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            off = mod.hooks_off(cwd=self.tmp)
        self.assertFalse(off)
        self.assertIn("could not be read", err.getvalue())

    def test_notice_prints_once_per_session_across_two_calls(self):
        self._write_config("hooks: off\n")
        mod = self._load()
        first = io.StringIO()
        with mock.patch("sys.stderr", first):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "sX"}, cwd=self.tmp))
        second = io.StringIO()
        with mock.patch("sys.stderr", second):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "sX"}, cwd=self.tmp))
        self.assertIn("hooks are off", first.getvalue())
        self.assertEqual(second.getvalue(), "")

    def test_a_different_session_gets_its_own_notice(self):
        self._write_config("hooks: off\n")
        mod = self._load()
        with mock.patch("sys.stderr", io.StringIO()):
            mod.hooks_off(payload={"session_id": "sA"}, cwd=self.tmp)
        second = io.StringIO()
        with mock.patch("sys.stderr", second):
            mod.hooks_off(payload={"session_id": "sB"}, cwd=self.tmp)
        self.assertIn("hooks are off", second.getvalue())

    def test_find_repo_root_walks_up_from_a_subdirectory(self):
        sub = os.path.join(self.tmp, "a", "b", "c")
        os.makedirs(sub, exist_ok=True)
        mod = self._load()
        self.assertEqual(os.path.realpath(mod.find_repo_root(sub)),
                         os.path.realpath(self.tmp))

    def _registered_hook_commands(self):
        """Every (event, script_filename, extra_argv) hooks/hooks.json
        actually registers, read from the file itself rather than
        retyped."""
        hooks_json = os.path.join(ROOT, "hooks", "hooks.json")
        with io.open(hooks_json, encoding="utf-8") as f:
            spec = json.load(f)
        pat = re.compile(r'tools/([\w.]+\.py)"?\s*(.*)')
        out = []
        for event, entries in spec["hooks"].items():
            for entry in entries:
                for h in entry.get("hooks", []):
                    m = pat.search(h.get("command", ""))
                    if not m:
                        continue
                    script = m.group(1)
                    tail = m.group(2).strip()
                    argv = [a.strip('"') for a in tail.split()] if tail else []
                    out.append((event, script, argv))
        return out

    def test_every_registered_hook_exits_zero_and_stays_inert_when_off(self):
        """The done_check's behavioral proof: every hook hooks/hooks.json
        wires up, run as a real subprocess against a repository with
        hooks turned off, exits 0 and prints nothing to stdout (no
        decision, no digest, no output at all), which means it stopped at
        the gate instead of doing its normal work.

        Narrowed 2026-09-04 (security review, Major): the WRITE GUARDS are
        exempt from the inertness claim on purpose, because a repository
        may not switch off the check standing between it and the person's
        disk. They are driven the other way, as guards, by
        test_the_write_guards_are_not_switched_off_by_the_repo_config below
        and by test_sbe_bash_guard.py. The exemption is read from
        sbe_repo_scope's own tuple rather than retyped."""
        self._write_config("hooks: off\n")
        guards = self._load().WRITE_GUARD_HOOKS
        commands = self._registered_hook_commands()
        self.assertGreater(
            len(commands), 0,
            "hooks.json parsed to zero commands; the regex or the fixture drifted")
        for i, (event, script, argv) in enumerate(commands):
            if script in guards:
                continue
            payload = json.dumps({
                "cwd": self.tmp, "session_id": "sess-%d" % i,
                "hook_event_name": event, "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(self.tmp, "x.py")},
                "transcript_path": os.path.join(self.tmp, "nonexistent.jsonl"),
                "reason": "test",
            })
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, script)] + argv,
                input=payload, capture_output=True, text=True, cwd=self.tmp)
            self.assertEqual(
                r.returncode, 0,
                "%s %s (%s) did not exit 0 with hooks off: stderr=%r"
                % (script, argv, event, r.stderr))
            self.assertEqual(
                r.stdout, "",
                "%s %s (%s) printed to stdout with hooks off, so it did its "
                "normal work instead of stopping at the gate: %r"
                % (script, argv, event, r.stdout))

    def test_the_write_guards_are_not_switched_off_by_the_repo_config(self):
        """Security review 2026-09-04, Major. .brother/config is content
        that arrives WITH a repository, and "hooks: off" used to make every
        registered hook of both products exit at once, the write guards
        included. So a clone could switch off the check standing between it
        and the person's disk, with one stderr line as the only trace.
        Here at the mechanism: the SAME config, read twice, answers True
        for an ordinary hook and False for a write guard, and each named
        guard's own source really passes the flag, so a call site edited
        back to the plain call fails here instead of silently reopening the
        hole. The refusal itself is driven end to end in
        test_sbe_bash_guard.py."""
        self._write_config("hooks: off\n")
        mod = self._load()
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "g1"}, cwd=self.tmp))
            self.assertFalse(
                mod.hooks_off(payload={"session_id": "g1"}, cwd=self.tmp,
                              write_guard=True))
        for script in ("sbe_authority_hook.py", "sbe_bash_write_guard.py"):
            self.assertIn(script, mod.WRITE_GUARD_HOOKS)
            with io.open(os.path.join(HERE, script), encoding="utf-8") as f:
                source = f.read()
            self.assertIn(
                "write_guard=True", source,
                "%s is named a write guard but does not pass "
                "write_guard=True, so .brother/config can switch it off "
                "again" % script)

    def test_absent_config_leaves_a_hook_unaffected(self):
        """No .brother/config at all: compare two runs of the same hook,
        one with no config file at all present, to prove E76 introduced no
        behavior change on the common path. sbe_bash_write_guard.py is a
        write-scoped PreToolUse hook, so an inert Bash command (no writes)
        is the natural inert-baseline case."""
        def run():
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "sbe_bash_write_guard.py")],
                input=json.dumps({
                    "cwd": self.tmp, "session_id": "sess-plain",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hi"},
                }),
                capture_output=True, text=True, cwd=self.tmp)
        baseline = run()
        after = run()
        self.assertEqual(baseline.returncode, 0)
        self.assertEqual(baseline.returncode, after.returncode)
        self.assertEqual(baseline.stdout, after.stdout)
        self.assertNotIn("hooks are off", after.stdout)

    # --- E50: the INSTALL-side default. The marker tools/install.py writes
    # beside the Claude settings directory turns the absent-config case from
    # ACTIVE into INACTIVE, so a machine that installed BrotherSBE for one
    # project runs nothing anywhere else.

    def _scoped_config_dir(self, text=None):
        """A throwaway CLAUDE_CONFIG_DIR carrying the E50 scope marker."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if text is None:
            text = self._load().SCOPE_MARKER_TEXT
        with io.open(os.path.join(d, "brother-hook-scope"), "w",
                     encoding="utf-8") as f:
            f.write(text)
        return d

    def _with_config_dir(self, d):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = d

        def restore():
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        self.addCleanup(restore)

    def test_scoped_install_makes_an_unopted_repository_inactive(self):
        self._with_config_dir(self._scoped_config_dir())
        mod = self._load()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            off = mod.hooks_off(payload={"session_id": "s50"}, cwd=self.tmp)
        self.assertTrue(off)
        self.assertEqual(err.getvalue(), "",
                         "a repository nobody opted in must cost nothing, "
                         "not even a line of stderr")

    def test_opting_a_repository_in_makes_it_active_again(self):
        self._with_config_dir(self._scoped_config_dir())
        mod = self._load()
        self._write_config(mod.ON_LINE + "\n")
        self.assertFalse(mod.hooks_off(payload={"session_id": "s51"},
                                       cwd=self.tmp))

    def test_off_still_wins_inside_an_opted_in_repository(self):
        self._with_config_dir(self._scoped_config_dir())
        mod = self._load()
        self._write_config("hooks: off\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(mod.hooks_off(payload={"session_id": "s52"},
                                          cwd=self.tmp))

    def test_no_marker_keeps_the_e76_default_so_a_clone_is_unchanged(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._with_config_dir(d)
        mod = self._load()
        self.assertFalse(mod.hooks_off(cwd=self.tmp))

    def test_a_marker_without_the_scope_line_scopes_nothing(self):
        self._with_config_dir(self._scoped_config_dir("scope: everything\n"))
        mod = self._load()
        self.assertFalse(mod.hooks_off(cwd=self.tmp))

    def test_an_unreadable_marker_stays_active_with_one_diagnostic(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "brother-hook-scope"), "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        self._with_config_dir(d)
        mod = self._load()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            off = mod.hooks_off(cwd=self.tmp)
        self.assertFalse(off, "a broken marker must never be the reason a "
                              "hook stops working")
        self.assertIn("could not be read", err.getvalue())

    @staticmethod
    def _tree(root):
        found = set()
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                found.add(os.path.relpath(os.path.join(dirpath, name), root))
        return found

    def test_the_inert_path_reads_nothing_and_finishes_well_under_50_ms(self):
        """E50's own done_check wording, timed in process because that is
        what measures BrotherSBE's work rather than a CPython start."""
        self._with_config_dir(self._scoped_config_dir())
        mod = self._load()
        before = self._tree(self.tmp)
        started = time.time()
        for i in range(20):
            self.assertTrue(mod.hooks_off(payload={"session_id": "t%d" % i},
                                          cwd=self.tmp))
        each_ms = (time.time() - started) * 1000.0 / 20
        self.assertEqual(self._tree(self.tmp), before,
                         "the inert path wrote into the repository")
        self.assertLess(each_ms, 50.0,
                        "the inert gate took %.3f ms per call; the row "
                        "promises under 50" % each_ms)
        sys.stderr.write("\n[E50] inert gate: %.3f ms per call\n" % each_ms)

    def test_every_registered_hook_is_inert_in_an_unopted_repository(self):
        """With a scoped install and NO .brother/config, every hook
        hooks/hooks.json registers, run as a real subprocess with its cwd
        inside an unrelated git repository, exits 0, prints nothing on
        either stream, and leaves the repository exactly as it found it."""
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self._scoped_config_dir()
        commands = self._registered_hook_commands()
        self.assertGreater(
            len(commands), 0,
            "hooks.json parsed to zero commands; the regex or the fixture drifted")
        before = self._tree(self.tmp)
        for i, (event, script, argv) in enumerate(commands):
            payload = json.dumps({
                "cwd": self.tmp, "project_dir": self.tmp,
                "session_id": "scoped-%d" % i,
                "hook_event_name": event, "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(self.tmp, "x.py")},
                "transcript_path": os.path.join(self.tmp, "nonexistent.jsonl"),
                "reason": "test",
            })
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, script)] + argv,
                input=payload, capture_output=True, text=True, cwd=self.tmp,
                env=env)
            self.assertEqual(
                r.returncode, 0,
                "%s %s (%s) did not exit 0 in an unopted repository: stderr=%r"
                % (script, argv, event, r.stderr))
            self.assertEqual(
                r.stdout, "",
                "%s %s (%s) printed to stdout in an unopted repository: %r"
                % (script, argv, event, r.stdout))
            self.assertEqual(
                r.stderr, "",
                "%s %s (%s) printed to stderr in an unopted repository: %r"
                % (script, argv, event, r.stderr))
        self.assertEqual(
            self._tree(self.tmp), before,
            "a hook wrote into an unopted repository; E50 promises it costs "
            "nothing at all")


class TestSbeInstallHookScope(unittest.TestCase):
    """E50, the install side of BrotherSBE: apply_hook_scope writes the
    marker, opts the RESOLVED TARGET in (this installer already knows which
    project was meant), and says where hooks are active in one line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config_dir = os.path.join(self.tmp, "claude")
        os.makedirs(self.config_dir)
        self.target = os.path.join(self.tmp, "project")
        os.makedirs(self.target)
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.config_dir

        def restore():
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        self.addCleanup(restore)
        spec = importlib.util.spec_from_file_location(
            "sbe_install_under_test", os.path.join(HERE, "install.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def _apply(self, hooks_everywhere=False, dry_run=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.apply_hook_scope(dry_run, self.target, hooks_everywhere)
        return out.getvalue()

    @property
    def marker(self):
        return os.path.join(self.config_dir, "brother-hook-scope")

    def test_the_default_scopes_the_machine_and_opts_the_target_in(self):
        out = self._apply()
        self.assertTrue(os.path.exists(self.marker),
                        "the default install left no scope marker, so its "
                        "hooks would run in every repository on the machine")
        with io.open(self.marker, encoding="utf-8") as f:
            self.assertIn("scope: repositories", f.read())
        cfg = os.path.join(self.target, ".brother", "config")
        with io.open(cfg, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "hooks: on")
        lines = [l for l in out.splitlines() if "hooks: active in" in l]
        self.assertEqual(len(lines), 1,
                         "expected exactly one line naming where hooks are "
                         "active; got %r" % lines)
        self.assertIn(self.target, lines[0])
        self.assertIn(".brother/config", lines[0],
                      "the line has to say how to add a repository")

    def test_an_existing_config_is_never_rewritten(self):
        """It may hold the "hooks: off" line somebody put there on purpose."""
        d = os.path.join(self.target, ".brother")
        os.makedirs(d)
        with io.open(os.path.join(d, "config"), "w", encoding="utf-8") as f:
            f.write("hooks: off\n")
        self._apply()
        with io.open(os.path.join(d, "config"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "hooks: off")

    def test_hooks_everywhere_writes_no_marker_and_removes_one(self):
        with io.open(self.marker, "w", encoding="utf-8") as f:
            f.write("scope: repositories\n")
        out = self._apply(hooks_everywhere=True)
        self.assertFalse(os.path.exists(self.marker),
                         "--hooks-everywhere left a marker behind, so the "
                         "install it printed is not the install it made")
        self.assertIn("EVERY repository", out)

    def test_a_dry_run_writes_nothing(self):
        out = self._apply(dry_run=True)
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(
            os.path.join(self.target, ".brother", "config")))
        self.assertIn("would:", out)

    def test_the_flag_is_parsed(self):
        self.assertEqual(self.mod._parse_args(["--hooks-everywhere"])[3], True)
        self.assertEqual(self.mod._parse_args([])[3], False)


class TestAnUnparseableRepoScopeModuleDegradesToActive(unittest.TestCase):
    """E76: four hooks load tools/sbe_repo_scope.py by path and swallow every
    exception, returning None. That None SILENTLY degrades the gate to
    "hooks active", and until this test no test in either product named the
    fallback at all: PR #145 annotated all six swallows as reviewed
    exemptions and shipped no test.

    Driven differentially, because a silent degrade cannot be seen any other
    way: the same repository with hooks turned off, run once against the real
    gate module and once against an unparseable one."""

    #: The four hooks whose loaders PR #145 exempted, with the argv
    #: hooks.json registers for each.
    HOOKS = (("sbe_session_reconcile.py", []),
             ("sbe_bash_write_guard.py", []),
             ("sbe_fence_hook.py", []),
             ("sbe_autosave.py", ["precompact"]))

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="sbe-repo-scope-repo-")
        self._scratch = [self.repo]
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        os.makedirs(os.path.join(self.repo, ".brother"))
        with io.open(os.path.join(self.repo, ".brother", "config"), "w",
                     encoding="utf-8") as f:
            f.write("hooks: off\n")

    def tearDown(self):
        for path in self._scratch:
            shutil.rmtree(path, ignore_errors=True)

    def _tools_farm(self, gate_module_text=None):
        """A symlink farm of the whole product root whose tools/ holds every
        real sibling except sbe_repo_scope.py, written from
        `gate_module_text` (None: symlink the real one). A farm rather than
        a copy because these hooks resolve siblings from their own directory
        and the product root above it."""
        base = tempfile.mkdtemp(prefix="sbe-repo-scope-farm-")
        self._scratch.append(base)
        for name in os.listdir(ROOT):
            if name != "tools":
                os.symlink(os.path.join(ROOT, name), os.path.join(base, name))
        tools = os.path.join(base, "tools")
        os.makedirs(tools)
        for name in os.listdir(HERE):
            if gate_module_text is not None and name == "sbe_repo_scope.py":
                continue
            os.symlink(os.path.join(HERE, name), os.path.join(tools, name))
        if gate_module_text is not None:
            with io.open(os.path.join(tools, "sbe_repo_scope.py"), "w",
                         encoding="utf-8") as f:
                f.write(gate_module_text)
        return tools

    def _run(self, tools, script, argv):
        payload = json.dumps({
            "cwd": self.repo, "session_id": "degrade-probe",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi",
                           "file_path": os.path.join(self.repo, "x.py")},
            "transcript_path": os.path.join(self.repo, "nonexistent.jsonl"),
            "reason": "test"})
        return subprocess.run(
            [sys.executable, os.path.join(tools, script)] + argv,
            input=payload, capture_output=True, text=True, cwd=self.repo)

    def test_no_hook_is_taken_down_by_an_unreadable_gate_module(self):
        good = self._tools_farm()
        bad = self._tools_farm(
            gate_module_text="def hooks_off(  # deliberately unparseable\n")
        differed = []
        for script, argv in self.HOOKS:
            before = self._run(good, script, argv)
            after = self._run(bad, script, argv)
            self.assertEqual(before.returncode, 0,
                             "%s with a working gate module: stderr=%r"
                             % (script, before.stderr))
            self.assertEqual(
                after.returncode, 0,
                "%s: an unparseable gate module must never take the hook "
                "down with it, it must degrade to active; stderr=%r"
                % (script, after.stderr))
            if before.stdout != after.stdout:
                differed.append(script)
        self.assertTrue(
            differed,
            "every hook behaved identically with a working and an "
            "unparseable gate module, so this test proves nothing about the "
            "degrade: it would pass on hooks that never consulted the gate "
            "at all. Hooks tried: %r" % [s for s, _ in self.HOOKS])


class TestEveryShippedExampleStillPassesItsOwnGate(unittest.TestCase):
    """docs/for-engineers/examples/ ships executable teaching scripts and a
    published figure (model-promotion's two AUC derivations and their
    threshold check). Until this test, no registered test ran sbe_gate over
    any examples directory: the PASS existed only as a done-check quoted by
    hand into the roadmap, so an edit to a published number, or to a
    derivation behind one, went unnoticed by the whole battery."""

    def _gate(self, directory):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_gate.py"), directory,
             "--strict"], capture_output=True, text=True)

    def test_sbe_gate_strict_exits_zero_for_each_example_directory(self):
        base = os.path.join(ROOT, "docs", "for-engineers", "examples")
        names = sorted(n for n in os.listdir(base)
                       if os.path.isdir(os.path.join(base, n)))
        self.assertTrue(names, "no example directories under %s" % base)
        for name in names:
            proc = self._gate(os.path.join(base, name))
            self.assertEqual(
                proc.returncode, 0,
                "examples/%s: sbe_gate --strict exited %d\n%s"
                % (name, proc.returncode, proc.stdout + proc.stderr))

    def test_every_recorded_rerun_value_is_what_the_derivation_prints_today(self):
        """What the gate above does NOT do. `numbers` reads the manifest's
        `rerun` block as a RECORDED CLAIM and re-executes nothing, so a
        published figure whose derivation has drifted still reads PASS.
        Measured, not assumed: adding 0.01 to compute_auc_rank.py's printed
        value left `numbers PASS` and exit 0 on the whole directory. This
        test runs the two commands the manifest itself names and compares
        what they print now against the values recorded beside them, which
        is the part that actually pins the number."""
        base = os.path.join(ROOT, "docs", "for-engineers", "examples")
        manifests = sorted(glob.glob(
            os.path.join(base, "*", "numbers-manifest.json")))
        self.assertTrue(manifests, "no numbers-manifest.json under %s" % base)
        checked = 0
        for manifest in manifests:
            where = os.path.dirname(manifest)
            with io.open(manifest, encoding="utf-8") as f:
                spec = json.load(f)
            for figure in spec.get("figures") or []:
                rerun = figure.get("rerun") or {}
                for value_key, command_key in (("primary", "query"),
                                               ("secondary",
                                                "second_derivation")):
                    recorded = rerun.get(value_key)
                    # The manifest documents each command with a trailing
                    # "# ..." note for a human reader; only the command
                    # itself is executable.
                    command = str(figure.get(command_key) or "").split("#")[0]
                    argv = command.split()
                    if recorded is None or not argv:
                        continue
                    if argv[0] not in ("python3", "python"):
                        continue  # not a command this test can run itself
                    argv[0] = sys.executable
                    proc = subprocess.run(argv, cwd=where, capture_output=True,
                                          text=True)
                    self.assertEqual(
                        proc.returncode, 0,
                        "%s: %r exited %d\n%s" % (manifest, command,
                                                  proc.returncode,
                                                  proc.stdout + proc.stderr))
                    self.assertEqual(
                        float(proc.stdout.strip()), float(recorded),
                        "%s: %s records %s for %r, but that command prints "
                        "%r today. Either the derivation drifted or the "
                        "published figure did; the gate cannot see this "
                        "because it re-runs nothing."
                        % (manifest, value_key, recorded, command,
                           proc.stdout.strip()))
                    checked += 1
        self.assertGreater(
            checked, 0,
            "no recorded rerun value was actually re-executed, so this test "
            "proved nothing about any published figure")


class TestTheReadmeVerificationBlockNamesTheVerifierFirst(unittest.TestCase):
    """One assertion per claim an outside audit of the public v1.0.2 tag
    corrected on this front page (row E112).

    The audit read the README as a newcomer checking an installation and found
    the block told them to run `sh scripts/checksums.sh` FIRST, which rewrites
    the very manifest they were about to trust: a tampered tree then verifies
    clean against its own fresh manifest. It also found the block claiming the
    verifier "refuses a missing, extra, or changed shipped file" while the
    verifier itself reports 125 entries under excluded paths and says it proves
    only agreement with the manifest it was handed, and it found a negative
    capability claim ("does not approve, merge, release, or deploy") that named
    no evidence at all.

    These are assertions about ORDER and WORDING because that is what was
    wrong. Prose is the interface here, and a front page that names a writer
    before a verifier teaches an unsafe habit however correct both commands are.
    """

    def setUp(self):
        with io.open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.readme = fh.read()

    def test_the_verifier_is_named_before_the_manifest_writer(self):
        verify = self.readme.find("scripts/verify-install.sh")
        write = self.readme.find("scripts/checksums.sh")
        self.assertNotEqual(verify, -1, "the README no longer names the verifier at all")
        self.assertNotEqual(write, -1, "the README no longer names the manifest writer at all")
        self.assertLess(
            verify, write,
            "the README names scripts/checksums.sh (the manifest WRITER, at offset %d) "
            "before scripts/verify-install.sh (the VERIFIER, at offset %d). A reader "
            "checking an installation follows the order on the page, and that order "
            "destroys the reference before it is read." % (write, verify))

    def test_the_page_says_plainly_that_the_writer_rewrites_the_manifest(self):
        self.assertIn(
            "rewrites CHECKSUMS.sha256", self.readme,
            "the README describes scripts/checksums.sh without saying it REWRITES "
            "CHECKSUMS.sha256. 'refreshes the manifest' reads to a newcomer as an "
            "update that preserves what was there.")
        self.assertIn(
            "replaces the reference you meant to check against", self.readme,
            "the README no longer states the consequence of running the writer before "
            "the verifier, which is the whole reason the order matters")

    def test_the_verifier_claim_is_narrowed_to_what_the_verifier_proves(self):
        self.assertNotIn(
            "refuses a missing, extra, or changed shipped file", self.readme,
            "the README still makes the broad claim the verifier does not support: it "
            "compares against the manifest it is HANDED and reports excluded paths "
            "separately, so it cannot refuse a changed file the manifest never named")
        self.assertIn(
            "agree with the manifest it was handed", self.readme,
            "the README no longer states what the verifier actually proves")
        self.assertIn(
            "excluded paths separately", self.readme,
            "the README no longer mentions that the verifier counts excluded paths "
            "apart, which is the gap between what it prints and what a reader assumes")
        self.assertIn(
            "does not authenticate", self.readme,
            "the README no longer says the verifier cannot authenticate the manifest, "
            "which the verifier itself warns about in its own output")

    def test_the_no_merge_claim_names_the_check_that_establishes_it(self):
        """A negative capability claim either names its evidence or is not
        called proven. The claim about merge, rebase, push and deploy HAS a
        check, so the page names it; approval and release do not, so the page
        says they are a design limit. This test also proves the named check
        exists, because a citation to a test nobody can find is the same empty
        claim in a more confident register."""
        self.assertIn(
            "TestNoMergeLaw", self.readme,
            "the README claims the tool does not merge, push or deploy without naming "
            "the check that establishes it")
        named = os.path.join(ROOT, "tools", "test_sbe_work.py")
        self.assertTrue(os.path.isfile(named),
                        "the README cites %s and it does not exist" % named)
        with io.open(named, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("class TestNoMergeLaw", body,
                      "the README cites TestNoMergeLaw and tools/test_sbe_work.py does "
                      "not define it")
        self.assertIn(
            "design limit", self.readme,
            "approval and release are not established by any check here, so the page "
            "must say so rather than carry them inside a proven-sounding sentence")
        self.assertNotIn(
            "The tool does not approve, merge, release, or deploy.", self.readme,
            "the unevidenced four-verb claim is back on the front page")


if __name__ == "__main__":
    unittest.main(verbosity=2)
