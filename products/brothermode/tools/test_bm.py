#!/usr/bin/env python3
"""BrotherMode regression tests. Standard library only (no pip install), matching
the zero-dependency ethos of the tools. Run: python3 tools/test_bm.py

These exist because an external review found a real secret-leak in the resume
brief that a test would have caught. Each test here guards a claim the project
makes about itself: secrets are redacted, sensitive files are owner-only, project
identity does not collide, and the autosave captures untracked work non-invasively.
"""
import ast, contextlib, glob, io, os, json, re, shutil, sqlite3, stat, sys, tempfile, time, subprocess, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))

# Import bm_telemetry as a module regardless of cwd.
spec = importlib.util.spec_from_file_location("bm_telemetry", os.path.join(HERE, "bm_telemetry.py"))
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)
sys.modules["bm_telemetry"] = bm

# Import bm_store as a module regardless of cwd, the same shape used above for
# bm_telemetry and the same shape test_bm_store.py uses for itself. Phase 3
# (ratified spec 2026-07-26-phase3-rewire-design.md) wired bm_threads.py onto
# this store; bm_registry.py is deleted. Every test below that needs to
# inspect a record's real state reads it from here, never from a hand-parsed
# JSON file, because there is no JSON file that holds ownership truth anymore.
_store_spec = importlib.util.spec_from_file_location("bm_store", os.path.join(HERE, "bm_store.py"))
bs = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(bs)
sys.modules["bm_store"] = bs

# Import bm_threads.py as a module too (not only via subprocess), so
# calibration tests can monkeypatch a real PRODUCT symbol on THIS module
# object and have bm_threads' own command functions, called in-process,
# actually run through the patched version. A subprocess re-execs
# bm_threads.py fresh from disk and can never see an in-process monkeypatch,
# so behavioral tests use _run_threads (subprocess, realistic end-to-end),
# and only calibration tests use this module object directly.
_threads_spec = importlib.util.spec_from_file_location("bm_threads", os.path.join(HERE, "bm_threads.py"))
threads_mod = importlib.util.module_from_spec(_threads_spec)
_threads_spec.loader.exec_module(threads_mod)
sys.modules["bm_threads"] = threads_mod

# bm_learn.py as a module object, for the same reason and under the same rule
# as bm_threads above: behavioural tests drive the real binary by subprocess,
# and ONLY calibration tests touch this object, to reinject a defect onto a
# real product symbol in-process where a subprocess could never see it.
_learn_spec = importlib.util.spec_from_file_location("bm_learn", os.path.join(HERE, "bm_learn.py"))
learn_mod = importlib.util.module_from_spec(_learn_spec)
_learn_spec.loader.exec_module(learn_mod)
sys.modules["bm_learn"] = learn_mod


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _call_thread_cmd_in_process(root, fn, argv):
    """Invoke a bm_threads command FUNCTION directly, in-process, so a
    monkeypatch on threads_mod's own module object is actually exercised
    (a subprocess re-execs bm_threads.py fresh and could never see it).
    Both BROTHERMODE_ROOT (resolve_root() checks it before cwd) AND the
    process cwd are set and restored: cwd matters too, because claim()
    resolves relative --files paths against os.getcwd(), which cmd_start
    passes through unchanged. Returns (exit_code, printed_stdout); a
    command that never calls sys.exit() returns 0."""
    old_env = os.environ.get("BROTHERMODE_ROOT")
    old_cwd = os.getcwd()
    os.environ["BROTHERMODE_ROOT"] = root
    os.chdir(root)
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out):
            try:
                fn(argv)
            except SystemExit as e:
                code = e.code or 0
    finally:
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("BROTHERMODE_ROOT", None)
        else:
            os.environ["BROTHERMODE_ROOT"] = old_env
    return code, out.getvalue()


def _run_threads(args, cwd, env=None):
    """Invoke bm_threads.py as a subprocess, always with BROTHERMODE_ROOT
    scrubbed from the child's environment (matching test_bm_store.py's own
    _run_cli), so ambient developer state can never leak into a test that is
    trying to prove something about root resolution or refusal behaviour."""
    e = dict(os.environ)
    e.pop("BROTHERMODE_ROOT", None)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "bm_threads.py")] + list(args),
        cwd=cwd, capture_output=True, text=True, env=e)


def _dump(root, raw=False):
    """Read the store through dump(). raw=True is what a TEST asserting what
    actually landed IN the store wants: LOOP 11's export policy withholds
    founder prose from the ordinary view, which is the right behavior for an
    export and the wrong read for a test that is checking storage. Tests that
    assert the POLICY itself call this with raw=False (the default)."""
    store = bs.Store(root, create=False)
    try:
        return store.dump(raw=raw)
    finally:
        store.close()


def _record(root, name, states=None):
    """The store's record for `name` (optionally restricted to a set of
    states), most-recently-updated first, or None. A thin test helper
    mirroring bm_threads._find_record, written independently so a bug in
    that function cannot also hide from the test meant to catch it."""
    data = _dump(root, raw=True)
    matches = [r for r in data["records"] if r["name"] == name]
    if states:
        matches = [r for r in matches if r["state"] in states]
    if not matches:
        return None
    matches.sort(key=lambda r: r["updated_at"], reverse=True)
    return matches[0]


def _claims(root, lifecycle_uuid):
    data = _dump(root, raw=True)
    return sorted(c["path"] for c in data["claims"] if c["lifecycle_uuid"] == lifecycle_uuid)


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


def _cli_receipt(runner, root, cid):
    """Mint a real approval receipt THROUGH THE CLI and return its token.

    Every CLI approval in this suite goes through here, so no test can keep
    passing against the receipt-free approval path the product no longer has.
    --json is used rather than scraping the human output, because the human
    output is the one place a token is ever printed and parsing it by position
    would break the moment that wording changes.

    No shaping flags: the sites that call this approve the candidate exactly as
    captured, which is what the receipt is then bound to."""
    g = runner(root, ["grant-approval", cid, "--answer",
                      "the founder answered yes, in a test", "--json"])
    assert g.returncode == 0, g.stderr
    return json.loads(g.stdout)["token"]


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

    def test_private_key_is_masked_as_a_block_not_just_its_header(self):
        # Finding 7: SECRET_PATTERNS matched only the -----BEGIN----- line, so
        # every line of base64 key material after it went to disk unmasked,
        # while SECURITY.md publicly claims private keys are redacted.
        key = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEAx7Vv9kQm2bYh3JqL8sFdTnW4pRzC5aXeUgHiN0oPvBtMcSyD\n"
               "QeKlZrXfAoGBAJk2TtVuWqLmNbHcYdRxPjEgFsAoZnKUvIwCyMlBtDqSeRhXfGpO\n"
               "n0aUyLcVdMwEtIbKqPjRzXsHfGnAoWlYcTuDeMkQvBpSrZiNxJgHdFaOcVtLmEyU\n"
               "-----END RSA PRIVATE KEY-----")

        clean, n = bm.redact("here is the deploy key:\n" + key + "\nplease rotate it")
        self.assertGreater(n, 0, "no redaction fired on a private key block")
        for line in key.splitlines():
            self.assertNotIn(line, clean, "key material survived redaction: %s" % line[:20])
        self.assertNotIn("MIIEowIBAAKCAQEAx7Vv", clean, "the key body was written out verbatim")
        self.assertIn("please rotate it", clean, "redaction swallowed the surrounding prose")
        self.assertIn("here is the deploy key", clean,
                      "redaction swallowed the prose before the key")

    def test_private_key_header_alone_still_masks_what_follows_it(self):
        # The reviewer's planted secret ended at the header line, with the body
        # (or nothing) after it. A truncated block must still not leak.
        clean, n = bm.redact("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx7Vv9kQm2bYh3JqL")
        self.assertGreater(n, 0)
        self.assertNotIn("MIIEowIBAAKCAQEAx7Vv9kQm2bYh3JqL", clean)

    def test_private_key_pattern_does_not_eat_ordinary_prose(self):
        clean, n = bm.redact(
            "-----BEGIN RSA PRIVATE KEY----- was pasted into the ticket yesterday "
            "and we should ask the platform team to rotate it")
        self.assertIn("ask the platform team to rotate it", clean,
                      "the private key pattern over-matched into ordinary prose")

    def test_intent_redacts_secret_but_preserves_content(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            # N-6 finding 9 (2026-08-04): cmd_intent gained its own consent
            # gate, the same shape TestResumeBrief already documents for
            # cmd_precompact_brief's own gate. This test is about REDACTION
            # and FILE MODE, so it supplies consent and keeps testing
            # exactly what it always did; the pre-consent behavior has its
            # own tests in TestConsentGateOnCheckUpdateAndIntent above.
            env = _consented_env(repo, v)
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_telemetry.py"), "intent",
                 "next: rotate the key, because PROD_DB_PASSWORD=hunter2 leaked"],
                env=env, cwd=repo, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            intent_files = glob.glob(os.path.join(v, "99-System", "telemetry", "intent-*.log"))
            self.assertEqual(len(intent_files), 1, "intent log not written")
            body = io.open(intent_files[0]).read()
            self.assertNotIn("hunter2", body, "intent log leaked the secret")
            self.assertIn("[REDACTED]", body)
            self.assertIn("rotate the key", body)
            self.assertIn("leaked", body)

    def test_rate_redacts_secret_but_preserves_content(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_telemetry.py"), "rate",
                 "--score", "4",
                 "--task", "rotate creds, api_key=sk-ant-api03-ABCDEFGHIJKLMNOP",
                 "--note", "found while reviewing the staging bucket"],
                env=env, cwd=repo, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            ratings_path = os.path.join(v, "99-System", "telemetry", "ratings.jsonl")
            self.assertTrue(os.path.exists(ratings_path), "ratings.jsonl not written")
            body = io.open(ratings_path).read()
            self.assertNotIn("sk-ant-api03-ABCDEFGHIJKLMNOP", body, "ratings.jsonl leaked the secret")
            self.assertIn("[REDACTED]", body)
            self.assertIn("rotate creds", body)
            self.assertIn("staging bucket", body)


class TestFounderNoteFileModes(unittest.TestCase):
    """Finding 8: ratings.jsonl and reviews.jsonl carry founder-written note
    text, so they are as sensitive as corrections.jsonl, which is already 0600.
    atomic_append defaults to 0644 and neither caller overrode it."""

    def _mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_ratings_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"), "rate",  # sbe: allow-silent test fixture writes ratings.jsonl; the mode/existence assertions right below verify the result, not the subprocess
                            "--score", "4", "--task", "ship the fence",
                            "--note", "the client contact said to keep this quiet"],
                           env=env, cwd=repo, capture_output=True, text=True)
            p = os.path.join(v, "99-System", "telemetry", "ratings.jsonl")
            self.assertTrue(os.path.exists(p), "ratings.jsonl not written")
            self.assertEqual(self._mode(p), 0o600,
                             "founder note text must be owner-only, got %o" % self._mode(p))

    def test_reviews_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),  # sbe: allow-silent test fixture writes reviews.jsonl; the mode/existence assertions right below verify the result, not the subprocess
                            "review-mark", "chased the client about the invoice"],
                           env=env, cwd=repo, capture_output=True, text=True)
            p = os.path.join(v, "99-System", "telemetry", "reviews.jsonl")
            self.assertTrue(os.path.exists(p), "reviews.jsonl not written")
            self.assertEqual(self._mode(p), 0o600,
                             "founder note text must be owner-only, got %o" % self._mode(p))

    def test_an_existing_world_readable_notes_file_is_tightened(self):
        # The two files already exist on this machine at 0644, and os.open only
        # applies its mode when it CREATES the file.
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            p = os.path.join(teld, "reviews.jsonl")
            io.open(p, "w").write("")
            os.chmod(p, 0o644)
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),  # sbe: allow-silent test fixture invokes review-mark on a pre-existing world-readable file; the mode assertion below verifies the outcome
                            "review-mark", "note"],
                           env=env, cwd=repo, capture_output=True, text=True)
            self.assertEqual(self._mode(p), 0o600,
                             "a pre-existing world-readable notes file must be tightened")

    def test_the_shared_ledger_is_not_tightened(self):
        # outcomes.jsonl is the shared ledger bm_score.py reads: only the
        # founder-note files change, nothing that is meant to be shared.
        self.assertEqual(bm.atomic_append.__defaults__, (0o644,),
                         "the default mode must stay 0644 for shared files")


class TestResumeBrief(unittest.TestCase):
    def test_brief_redacts_and_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["BROTHERMODE_VAULT"] = os.path.join(d, "vault")
            # A CONSENTED config is now a precondition of writing a brief at
            # all (2026-08-02): cmd_precompact_brief checks consent as its
            # first statement, because it was found writing the founder's
            # last message verbatim into a vault on machines where setup had
            # never run. This test is about REDACTION and FILE MODE, so it
            # supplies consent and keeps testing exactly what it always did;
            # the pre-consent behavior has its own tests in
            # tools/test_bm_consent.py. BROTHERME_CONFIG is the documented
            # override for the config path (scripts/setup.py config_path).
            cfg = os.path.join(d, "brotherme-config.json")
            with io.open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"setup_complete": True,
                           "vault_path": os.environ["BROTHERMODE_VAULT"],
                           "privacy_notice_version": "2026-08-01",
                           "installation_mode": "clone",
                           "security_mode": "standard"}, fh)
            os.environ["BROTHERME_CONFIG"] = cfg
            self.addCleanup(os.environ.pop, "BROTHERME_CONFIG", None)
            # rebuild the module's paths against the temp vault
            import importlib
            importlib.reload(bm)
            repo = os.path.join(d, "acme", "backend")
            os.makedirs(repo)
            tp = os.path.join(d, "t.jsonl")
            msgs = [{"type": "user", "message": {"content": "the prod password is hunter2"}}]
            msgs += [{"type": "assistant", "message": {"content": [
                {"type": "text", "text": "using token sk-ant-api03-ABCDEFGHIJKLMNOP"},
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "curl -H 'Authorization: Bearer abcdef1234567890xyz' x"}}]}}]
            io.open(tp, "w").write("\n".join(json.dumps(m) for m in msgs))
            payload = json.dumps({"transcript_path": tp, "cwd": repo})
            old = sys.stdin
            sys.stdin = io.StringIO(payload)
            try:
                bm.cmd_precompact_brief()
            finally:
                sys.stdin = old
            teldir = os.path.join(os.environ["BROTHERMODE_VAULT"], "99-System", "telemetry")
            briefs = [f for f in os.listdir(teldir) if f.startswith("last-resume-")]
            self.assertEqual(len(briefs), 1)
            path = os.path.join(teldir, briefs[0])
            body = io.open(path).read()
            for secret in ("hunter2", "sk-ant-api03-ABCDEFGHIJKLMNOP", "abcdef1234567890xyz"):
                self.assertNotIn(secret, body, "resume brief leaked: %s" % secret)
            self.assertIn("[REDACTED]", body)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600, "resume brief must be owner-only, got %o" % mode)

    def test_brief_carries_a_stable_id_that_survives_the_rewrite(self):
        """D05 (Stable IDs), driven backwards from the benchmark failure it
        reproduces: the vault benchmark walks every .md under the vault and
        fails a note with no id: in its frontmatter. cmd_precompact_brief is
        the writer of last-resume-*.md, and before this fix it never emitted
        one. Proves three things a hand backfill of the existing file could
        not: a freshly written brief carries a well-formed id, the SAME id
        survives the file being truncated and rewritten whole at the next
        compaction (the writer's own documented behavior), and two different
        project identities never collide on one id."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["BROTHERMODE_VAULT"] = os.path.join(d, "vault")
            cfg = os.path.join(d, "brotherme-config.json")
            with io.open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"setup_complete": True,
                           "vault_path": os.environ["BROTHERMODE_VAULT"],
                           "privacy_notice_version": "2026-08-01",
                           "installation_mode": "clone",
                           "security_mode": "standard"}, fh)
            os.environ["BROTHERME_CONFIG"] = cfg
            self.addCleanup(os.environ.pop, "BROTHERME_CONFIG", None)
            import importlib
            importlib.reload(bm)

            def write_brief(cwd):
                repo = os.path.join(d, cwd)
                os.makedirs(repo, exist_ok=True)
                tp = os.path.join(d, cwd.replace(os.sep, "_") + ".jsonl")
                msgs = [{"type": "user", "message": {"content": "continue the work"}}]
                io.open(tp, "w").write("\n".join(json.dumps(m) for m in msgs))
                payload = json.dumps({"transcript_path": tp, "cwd": repo})
                old = sys.stdin
                sys.stdin = io.StringIO(payload)
                try:
                    bm.cmd_precompact_brief()
                finally:
                    sys.stdin = old
                teldir = os.path.join(os.environ["BROTHERMODE_VAULT"], "99-System", "telemetry")
                briefs = [f for f in os.listdir(teldir) if f.startswith("last-resume-")]
                return teldir, sorted(briefs)

            ids_mod_path = os.path.join(HERE, "bm_vault_ids.py")
            spec = importlib.util.spec_from_file_location("ids_for_test", ids_mod_path)
            ids_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ids_mod)

            teldir, briefs = write_brief(os.path.join("acme", "one"))
            self.assertEqual(len(briefs), 1)
            path = os.path.join(teldir, briefs[0])
            first_id = ids_mod.read_id(io.open(path, encoding="utf-8").read())
            self.assertIsNotNone(first_id, "resume brief has no valid id: field")

            # Compaction fires again on the SAME identity: the file is
            # truncated and rewritten whole (cmd_precompact_brief's own
            # documented behavior). The id must not change underneath it.
            teldir, briefs = write_brief(os.path.join("acme", "one"))
            self.assertEqual(len(briefs), 1)
            path = os.path.join(teldir, briefs[0])
            second_id = ids_mod.read_id(io.open(path, encoding="utf-8").read())
            self.assertEqual(first_id, second_id,
                             "id changed across a rewrite of the same identity's brief")

            # A different identity gets its own brief and must not collide.
            teldir, briefs = write_brief(os.path.join("acme", "two"))
            self.assertEqual(len(briefs), 2)
            other = [f for f in briefs if f != os.path.basename(path)][0]
            other_id = ids_mod.read_id(io.open(os.path.join(teldir, other), encoding="utf-8").read())
            self.assertIsNotNone(other_id)
            self.assertNotEqual(first_id, other_id, "two identities collided on one id")


class TestProjectIdentity(unittest.TestCase):
    def test_same_basename_different_path_no_collision(self):
        a = bm._project_of("/tmp/client-a/backend")
        b = bm._project_of("/tmp/client-b/backend")
        self.assertNotEqual(a, b, "same-basename projects collided: %s == %s" % (a, b))
        self.assertEqual(a, bm._project_of("/tmp/client-a/backend"), "identity must be stable")


class TestAutosave(unittest.TestCase):
    def test_snapshot_captures_untracked_without_touching_tree(self):
        sh = os.path.join(HERE, "bm_autosave.sh")
        if not os.path.exists(sh):
            self.skipTest("bm_autosave.sh not present")
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
            env = dict(os.environ, BROTHERMODE_VAULT=vdir)
            subprocess.run(["sh", sh, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent precompact hook call whose effect is verified afterward via git status/rev-parse/refs reads
                           text=True, env=env)
            # working tree and branch untouched
            self.assertEqual(before, git("status", "--porcelain").stdout)
            self.assertEqual(before_head, git("rev-parse", "HEAD").stdout)
            # ref created and it contains the untracked file
            ref = git("rev-parse", "-q", "--verify", "refs/brothermode/autosave")
            self.assertEqual(ref.returncode, 0, "autosave ref was not created")
            shown = git("show", "refs/brothermode/autosave:untracked_new.txt").stdout
            self.assertIn("WIP-WORK", shown, "autosave did not capture the untracked file")
            # secret-shaped files must NOT enter the snapshot
            io.open(os.path.join(repo, ".env"), "w").write("SECRET=leak")
            subprocess.run(["sh", sh, "precompact"], input=json.dumps({"cwd": repo}),  # sbe: allow-silent second precompact call whose effect is verified afterward via git cat-file -e reading the snapshot
                           text=True, env=env)
            envobj = git("cat-file", "-e", "refs/brothermode/autosave:.env")
            self.assertNotEqual(envobj.returncode, 0, ".env leaked into the autosave snapshot")


class TestCompactHintHonesty(unittest.TestCase):
    """GATE E (fix-round 2026-07-26): compact-hint used to print "Your files
    are autosaved" UNCONDITIONALLY on every compaction resume, and named
    tools/bm_autosave.sh, which Phase 2 deleted. Fixed: the claim prints
    only when bm_autosave.has_receipt finds a real receipt for this
    worktree and session; otherwise an honest alternative names the
    command that actually exists (bm_autosave.py, not the deleted .sh).

    Two cases run the real CLI end to end as a subprocess (a receipt
    written by the real bm_autosave.py, and a store with none); the third
    patches the cached _BM_AUTOSAVE reference bm_telemetry itself loaded at
    import time (the PRODUCT symbol, not a local stand-in) to simulate
    bm_autosave.py being unavailable, since deleting or renaming the real
    file would cross this change's own fence."""

    def _git_repo(self, path):
        def git(*a):
            r = subprocess.run(["git", "-C", path] + list(a), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "git %s failed: %s" % (" ".join(a), r.stderr))
            return r
        git("init", "-q")
        git("config", "user.email", "t@t.t")
        git("config", "user.name", "t")
        io.open(os.path.join(path, "tracked.txt"), "w").write("v1")
        git("add", "-A")
        git("commit", "-qm", "init")

    def _init_store(self, repo, env):
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"), "init"],
                            cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "bm_store init failed: %s" % (r.stdout + r.stderr))

    def _compact_hint(self, repo, session_id, env):
        payload = json.dumps({"source": "compact", "cwd": repo, "session_id": session_id})
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "bm_telemetry.py"), "compact-hint"],
            input=payload, cwd=repo, env=env, capture_output=True, text=True)

    def test_claims_safety_only_when_a_real_receipt_exists(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as v:
            # Consented, like the other hook-driving tests in this file:
            # bm_autosave.py gained the same consent gate the telemetry
            # writer already had (Loop 3 finding I1), so an unconsented
            # precompact correctly writes no receipt at all, and a test
            # about what the hint says WHEN a receipt exists must create
            # one first.
            env = _consented_env(repo, v)
            env.pop("BROTHERMODE_ROOT", None)
            self._git_repo(repo)
            self._init_store(repo, env)
            # A snapshot of a working tree that matches HEAD writes no
            # receipt at all (nothing to save); dirty the tree first so
            # bm_autosave.py actually has something to capture.
            io.open(os.path.join(repo, "wip.txt"), "w").write("work in progress")
            snap = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_autosave.py"), "precompact"],
                input=json.dumps({"cwd": repo, "session_id": "sess-1"}),
                cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(snap.returncode, 0, "bm_autosave precompact failed: %s"
                              % (snap.stdout + snap.stderr))
            r = self._compact_hint(repo, "sess-1", env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Your files are autosaved", r.stdout,
                          "a real receipt exists; the claim must be printed")
            self.assertIn("bm_autosave.py recover", r.stdout,
                          "must name the recovery command that actually exists")
            self.assertNotIn("bm_autosave.sh", r.stdout,
                             "must never name the deleted shell script")

    def test_no_claim_when_no_receipt_matches_this_session(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as v:
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            env.pop("BROTHERMODE_ROOT", None)
            self._git_repo(repo)
            self._init_store(repo, env)
            # No snapshot was ever taken, so no receipt exists for any session.
            r = self._compact_hint(repo, "sess-never-saved", env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("Your files are autosaved", r.stdout,
                             "must not claim safety when no receipt was found")
            self.assertIn("No autosave snapshot receipt was found", r.stdout)
            self.assertIn("bm_autosave.py recover", r.stdout,
                          "must still name the recovery command that exists")
            self.assertNotIn("bm_autosave.sh", r.stdout)

    def test_no_claim_and_no_crash_when_bm_autosave_is_unavailable(self):
        old_mod, old_err = bm._BM_AUTOSAVE, bm._BM_AUTOSAVE_LOAD_ERROR
        bm._BM_AUTOSAVE = None
        bm._BM_AUTOSAVE_LOAD_ERROR = "simulated: bm_autosave.py unavailable"
        old_stdin, old_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(
                {"source": "compact", "cwd": "/tmp", "session_id": "s1"}))
            sys.stdout = captured = io.StringIO()
            bm.cmd_compact_hint()  # must never raise
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            bm._BM_AUTOSAVE, bm._BM_AUTOSAVE_LOAD_ERROR = old_mod, old_err
        out = captured.getvalue()
        self.assertNotIn("Your files are autosaved", out,
                         "must not claim safety when bm_autosave could not be loaded")
        self.assertIn("simulated: bm_autosave.py unavailable", out)
        self.assertNotIn("bm_autosave.sh", out)


class TestHandoff(unittest.TestCase):
    def test_handoff_redacts_and_preserves(self):
        with tempfile.TemporaryDirectory() as v:
            base = os.path.join(v, "10-Projects", "demo", "Sessions")
            os.makedirs(base)
            proj = os.path.dirname(base)
            io.open(os.path.join(proj, "Overview.md"), "w").write(
                "builds X. the prod password is hunter2")
            io.open(os.path.join(base, "s.md"), "w").write("used DB_PASSWORD=s3cr3t here")
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            with tempfile.TemporaryDirectory() as cwd:
                r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                    "handoff", "demo"], env=env, cwd=cwd,
                                   capture_output=True, text=True)
                out = os.path.join(cwd, "handoff-demo.md")
                self.assertTrue(os.path.exists(out), "handoff file not written")
                body = io.open(out).read()
                self.assertNotIn("hunter2", body)
                self.assertNotIn("s3cr3t", body)
                self.assertIn("builds X", body)
                self.assertIn("[REDACTED]", body)


class TestThreadMode(unittest.TestCase):
    """Thread mode's contract: nothing auto-flips, the cap holds, and switching
    OFF mid-project is LOSSLESS (the founder's hard requirement). Recalibrated
    for Phase 3: ownership lives in the store, so every assertion below reads
    the store's real state instead of a hand-parsed registry.json."""

    def _run(self, cwd, *args):
        return _run_threads(args, cwd)

    def test_recommend_never_flips_mode(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._run(d, "recommend", "5")
            self.assertIn("RECOMMENDATION", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, "threads", "thread-mode.json")),
                             "recommend must never flip or create mode state")
            self.assertFalse(os.path.exists(os.path.join(d, ".brothermode")),
                             "recommend must never create a store either")

    def test_cap_is_enforced(self):
        # Recalibrated: V1 read BROTHERMODE_MAX_THREADS to shrink the cap for
        # a fast test. bm_store.MAX_ACTIVE_PERSISTENT (3) is a fixed module
        # constant with no env override (a store-level capability gap flagged
        # in the implementer's report), so this exercises the real cap.
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            for n in ("a", "b", "c"):
                self._run(d, "start", n, "obj " + n, "--files", "api/%s.py" % n)
            r = self._run(d, "start", "dee", "one too many", "--files", "api/dee.py")
            self.assertEqual(r.returncode, 2)
            self.assertIn("refused (cap)", r.stdout)

    def test_off_is_lossless_and_parks(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "build payments", "--files", "api/pay.py")
            self._run(d, "checkpoint", "payments", "--decision", "chose Stripe",
                      "--topic", "payments-api", "--next", "wire webhook")
            r = self._run(d, "off")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            state = io.open(os.path.join(d, "STATE.md")).read()
            self.assertIn("chose Stripe", state, "decision lost when thread mode was turned off")
            self.assertIn("wire webhook", state, "next intent lost when thread mode was turned off")
            self.assertTrue(
                os.path.isdir(os.path.join(d, "threads"))
                and any(n.startswith("payments-") for n in os.listdir(os.path.join(d, "threads"))),
                "thread directory was deleted; it must stay resumable")
            rec = _record(d, "payments")
            self.assertEqual(rec["state"], "parked")

    def test_adopt_absorbs_a_dead_thread(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "search", "build search", "--files", "api/search.py")
            self._run(d, "checkpoint", "search", "--decision", "use trigram index",
                      "--topic", "search-impl")
            # --adopt-from-live-session: `start` and `adopt` each generate
            # their own session id here, so the store's live-session-adopt
            # gate (new in V2, see TestLiveSessionAdoptGate) always applies;
            # this is the founder deliberately confirming the takeover.
            r = self._run(d, "adopt", "search", "--adopt-from-live-session")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            state = io.open(os.path.join(d, "STATE.md")).read()
            self.assertIn("use trigram index", state, "adopted thread's context was orphaned")


class TestThreadSafety(unittest.TestCase):
    """The two defects a self-review found in thread mode, still true after
    Phase 3: secrets must never reach a file on disk unredacted."""

    def _run(self, cwd, *a):
        return _run_threads(a, cwd)

    def _thread_dir_name(self, d, prefix):
        return [n for n in os.listdir(os.path.join(d, "threads")) if n.startswith(prefix)][0]

    def test_secrets_never_reach_digest_or_absorbed_state(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "pay", "build pay", "--files", "api/pay.py")
            self._run(d, "checkpoint", "pay", "--decision",
                      "used PROD_DB_PASSWORD=hunter2 to test", "--topic", "creds",
                      "--next", "rotate it")
            tdir = self._thread_dir_name(d, "pay-")
            dig = io.open(os.path.join(d, "threads", tdir, "digest.md")).read()
            self.assertNotIn("hunter2", dig, "secret leaked into the thread digest")
            self._run(d, "off")
            st = io.open(os.path.join(d, "STATE.md")).read()
            self.assertNotIn("hunter2", st, "secret propagated into the project STATE.md")
            self.assertIn("rotate it", st, "redaction destroyed the real content")

    def test_secret_in_objective_does_not_reach_thread_files(self):
        # Finding: bm_threads.py's OWN thread-local STATE.md scaffold used to
        # embed the raw objective (fixed during this rewire, before this test
        # was written against it); the digest.md view was always safe because
        # it comes from bm_store.render_digest, which redacts internally.
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "pay", "build pay using PROD_DB_PASSWORD=hunter2 now",
                      "--files", "api/pay.py")
            tdir = self._thread_dir_name(d, "pay-")
            base = os.path.join(d, "threads", tdir)
            state_md = io.open(os.path.join(base, "STATE.md")).read()
            digest_md = io.open(os.path.join(base, "digest.md")).read()
            mode = json.load(io.open(os.path.join(d, "threads", "thread-mode.json")))
            self.assertNotIn("hunter2", state_md, "secret leaked into the thread's STATE.md")
            self.assertNotIn("hunter2", digest_md, "secret leaked into the thread's digest.md")
            self.assertNotIn("hunter2", json.dumps(mode),
                             "secret leaked into thread-mode.json (it must hold no ownership "
                             "data at all in the new architecture)")
            self.assertIn("[REDACTED]", state_md, "redaction destroyed the objective entirely")
            r = self._run(d, "dashboard")
            self.assertNotIn("hunter2", r.stdout, "secret leaked into the dashboard output")

    def test_dashboard_survives_a_mode_value_that_is_not_a_string(self):
        # The rest of V1's dashboard-survives-malformed-input sweep
        # (a thread entry that is not a dict, an objective that is not a
        # string) is structurally closed now: the mode file no longer has a
        # "threads" map or per-thread objective at all, so that shape of
        # corruption cannot occur. The mode VALUE itself can still be
        # hand-edited to something odd, so that one case is kept.
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "threads"))
            io.open(os.path.join(d, "threads", "thread-mode.json"), "w").write(
                json.dumps({"mode": 5}))
            r = self._run(d, "dashboard")
            self.assertNotIn("Traceback", r.stderr,
                             "the dashboard raised on a non-string mode value")
            self.assertIn("BROTHERMODE THREADS", r.stdout)

    def test_dashboard_on_older_schema_store_refuses_without_claiming_corruption(self):
        """Added 2026-08-04 with the wording fix. bm_threads.py's dashboard is
        the one path that printed "STORE CORRUPT" for a healthy store with no
        test covering it at all: it reads through ReadOnlyStore, so a store one
        schema behind this BrotherMode reached its StoreCorrupt handler. It now
        falls through to main()'s existing OwnershipRefused handler, which
        names the reason and exits 2. HOME and both vault variables are pinned
        into throwaway directories so this can never reach a real vault."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home, \
             tempfile.TemporaryDirectory() as vault:
            env = dict(HOME=home, BROTHERMODE_VAULT=vault, BROTHERSBE_VAULT=vault)
            _run_threads(("on",), d, env=env)
            r0 = _run_threads(("start", "search", "build search",
                               "--files", "api/search.py"), d, env=env)
            self.assertEqual(r0.returncode, 0, r0.stdout + r0.stderr)
            # LAST, and only through sqlite3: any writable bm_threads command
            # run after this point would migrate the store straight back up.
            conn = sqlite3.connect(bs.store_path(d))
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                             (str(bs.SCHEMA_VERSION - 1),))
                conn.execute("COMMIT")
            finally:
                conn.close()
            r = _run_threads(("dashboard",), d, env=env)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("STORE CORRUPT", r.stdout)
            self.assertIn("refused (schema-behind)", r.stdout)

    def test_concurrent_starts_all_register(self):
        # A thread invisible to the store is invisible to the dashboard AND
        # skipped by `off`, which would silently lose its context. The
        # store's own BEGIN IMMEDIATE serializes the writes; this proves the
        # CLI wrapper does not reintroduce a race on top of that guarantee.
        import threading as th
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            ts = [th.Thread(target=self._run, args=(d, "start", n, "obj",
                                                      "--files", "api/%s.py" % n))
                  for n in "abc"]
            [t.start() for t in ts]
            [t.join() for t in ts]
            data = _dump(d)
            names = sorted(r["name"] for r in data["records"] if r["state"] == "active")
            self.assertEqual(names, ["a", "b", "c"], "a concurrent start was lost from the store")


class TestOffReportsHonestly(unittest.TestCase):
    def _run(self, cwd, *a):
        return _run_threads(a, cwd)

    def test_off_says_incomplete_and_keeps_the_thread_alive(self):
        # Finding 5 (V1): `off` flipped the mode and reported "nothing to
        # absorb" while nothing had actually been parked, the exact opposite
        # of the truth. It must still name a real failure and refuse to flip.
        #
        # LOOP P12 changed WHICH failures can happen. The old version of this
        # test made STATE.md read-only, because STATE.md was where a handover
        # lived; it also skipped on most machines, since write_state_view
        # os.replace()s into a writable directory and a read-only file does
        # not stop it. Handovers are store rows now, so the failure that
        # genuinely blocks a drain is the store refusing the transition. That
        # is what is injected here, and the founder-visible contract is
        # unchanged: named failure, mode stays ON, record stays ACTIVE.
        with tempfile.TemporaryDirectory() as d:
            mod = _fresh_threads_module("off_incomplete")
            store_mod = mod._store()
            with contextlib.redirect_stdout(io.StringIO()):
                _run_threads(["on"], d)
                _run_threads(["start", "payments", "pay", "--files", "api/pay.py"], d)
                _run_threads(["checkpoint", "payments", "--next",
                              "wire the webhook handler"], d)
            real = store_mod.Store.transition

            def refuse(self_store, *a, **k):
                raise store_mod.OwnershipRefused(
                    "injected-refusal", "injected: the store refused this park")
            store_mod.Store.transition = refuse
            try:
                code, out = _call_thread_cmd_in_process(d, mod.cmd_off, [])
            finally:
                store_mod.Store.transition = real
            self.assertEqual(code, 2, out)
            self.assertIn("HANDOVER INCOMPLETE", out)
            self.assertIn("injected: the store refused this park", out,
                          "the founder must be told WHY: %s" % out)
            mode = json.load(io.open(os.path.join(d, "threads", "thread-mode.json")))
            self.assertEqual(mode["mode"], "on",
                             "mode must not flip to off when the drain failed")
            rec = _record(d, "payments")
            self.assertEqual(rec["state"], "active",
                             "the record must stay active so a retry can still absorb it")

    def test_off_reports_nothing_to_absorb_when_no_active_persistent_records_exist(self):
        # The other half of V1's finding 5: "nothing to absorb" must keep
        # meaning exactly that, so the two outcomes stay distinguishable.
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            r = self._run(d, "off")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to absorb", r.stdout)
            mode = json.load(io.open(os.path.join(d, "threads", "thread-mode.json")))
            self.assertEqual(mode["mode"], "off")

    def test_off_survives_a_malformed_mode_file(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "pay", "--files", "api/pay.py")
            self._run(d, "checkpoint", "payments", "--next", "wire the webhook handler")
            mp = os.path.join(d, "threads", "thread-mode.json")
            mode = json.load(io.open(mp))
            mode["history"] = "not-a-list"
            mode["some_stray_hand_edited_key"] = {"whatever": True}
            io.open(mp, "w").write(json.dumps(mode))
            r = self._run(d, "off")
            self.assertNotIn("Traceback", r.stderr, "off raised on a malformed mode file")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("wire the webhook handler", io.open(os.path.join(d, "STATE.md")).read())


class TestCapIsAtomic(unittest.TestCase):
    def test_a_cap_refused_start_leaves_no_store_record(self):
        # Finding 6 (V1): a refused start left an active record behind
        # anyway, so a later claim on those files was refused by a record
        # the dashboard never showed. claim() and _admit() together make
        # this atomic in the store now; this proves it end to end through
        # the CLI.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            for n in ("a", "b", "c"):
                _run_threads(["start", n, "obj", "--files", "api/%s.py" % n], d)
            r = _run_threads(["start", "dee", "one too many", "--files", "api/dee.py"], d)
            self.assertIn("refused (cap)", r.stdout)
            self.assertIsNone(_record(d, "dee"),
                             "a cap-refused start must leave no store record behind")


class TestAdopt(unittest.TestCase):
    """adopt is a write boundary and a fence release, and used to be neither."""

    def _run(self, cwd, *a):
        return _run_threads(a, cwd)

    # NOTE on --adopt-from-live-session, found while writing these tests:
    # bm_store.transition() refuses ('live-session-adopt-blocked') to move an
    # ACTIVE record to 'adopted' when the caller's session differs from the
    # one on file, unless adopt_from_live_session=True is passed explicitly.
    # `start` and `adopt` here each get their own freshly-generated session
    # id (no --session given), so they always differ, and the flag is
    # required for every one of these tests, not an edge case. This is the
    # store's OWN fail-closed design working as intended (adopting a record
    # that is still ACTIVE under a different, live session is a takeover
    # dressed as adoption); bm_threads.py must not default the flag to True,
    # or it would silently defeat that gate. See TestLiveSessionAdoptGate.

    def test_adopt_redacts_the_handover_it_delivers(self):
        # V1's adopt appended the thread's own hand-written STATE.md snippet
        # verbatim alongside the digest. V2's adopt delivers ONLY the store's
        # structured render_digest (objective/next-intent/blockers/files/
        # decisions), never arbitrary thread-local prose: a deliberate scope
        # reduction (see the implementer's report) that also means there is
        # exactly one redaction boundary to keep correct, not two. Any secret
        # that reaches STATE.md at all can only have arrived through a
        # checkpoint decision, so that is what this test drives through.
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "build payments", "--files", "api/pay.py")
            self._run(d, "checkpoint", "payments", "--next", "wire the webhook handler",
                      "--topic", "notes",
                      "--decision", "deploy key AKIAIOSFODNN7EXAMPLE password=hunter2swordfish")
            r = self._run(d, "adopt", "payments", "--adopt-from-live-session")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            state = io.open(os.path.join(d, "STATE.md")).read()
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", state, "adopt leaked an AWS key into STATE.md")
            self.assertNotIn("hunter2swordfish", state, "adopt leaked a password into STATE.md")
            self.assertIn("[REDACTED]", state, "nothing was redacted at all")
            self.assertIn("wire the webhook handler", state,
                          "redaction destroyed the real handover content")

    def test_adopt_does_not_leave_the_digest_to_be_absorbed_twice(self):
        # Finding 4 (V1): adopt flipped the thread but left the record
        # active, so `off` drained the same digest again. adopted is a
        # terminal state `off` never selects (it only ever selects active
        # persistent records), so this is closed structurally now.
        #
        # Asserts the DELIVERY TAG count, not a raw text count: STATE.md's
        # generated dashboard block (refreshed by both `adopt` and `off`)
        # legitimately repeats a record's "next intent" text as part of its
        # always-current view, which is not a duplicate delivery. Counting
        # tags is what V1's own most rigorous property test (I2) did for the
        # same reason.
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "build payments", "--files", "api/pay.py")
            self._run(d, "checkpoint", "payments", "--next", "wire the webhook handler")
            r = self._run(d, "adopt", "payments", "--adopt-from-live-session")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self._run(d, "off")
            state = io.open(os.path.join(d, "STATE.md")).read()
            tags = re.findall(r"brothermode-handover:[^\s>]+", state)
            self.assertEqual(len(tags), len(set(tags)),
                             "the adopted digest was delivered more than once: %s" % tags)
            self.assertIn("wire the webhook handler", state)

    def test_adopt_releases_the_file_fence(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "build payments", "--files", "api/pay.py")
            r = self._run(d, "adopt", "payments", "--adopt-from-live-session")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rec = _record(d, "payments")
            self.assertNotEqual(rec["state"], "active", "the adopted record must not stay active")
            r = self._run(d, "start", "billing", "bill", "--files", "api/pay.py")
            self.assertNotIn("overlap", r.stdout.lower(),
                             "an adopted thread's fence must be released")
            self.assertIsNotNone(_record(d, "billing", states=("active",)),
                                 "the successor thread was never registered")


class TestLiveSessionAdoptGate(unittest.TestCase):
    """New in V2: adopting a record that is still ACTIVE under a different,
    live session now requires an explicit --adopt-from-live-session, and is
    refused (not silently forced) without it. V1 had no such gate at all; it
    let ANY adopt succeed regardless of whether the original session might
    still be running, which is a takeover dressed as adoption."""

    def test_adopt_without_the_flag_is_refused_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments", "--files", "api/pay.py",
                         "--session", "sessA"], d)
            state_path = os.path.join(d, "STATE.md")
            before = _read(state_path) if os.path.exists(state_path) else None
            r = _run_threads(["adopt", "payments"], d)
            self.assertEqual(r.returncode, 2)
            self.assertIn("ADOPT REFUSED (live-session-adopt-blocked)", r.stdout)
            rec = _record(d, "payments", states=("active",))
            self.assertIsNotNone(rec, "a refused adopt must leave the record exactly as it was")
            self.assertEqual(rec["session_id"], "sessA")
            # GATE 3 (release-blockers spec, 2026-07-26): a refused adopt
            # used to still permanently write its handover into STATE.md
            # (delivery happened before the transition). Reproduced: a LIVE
            # thread got recorded as "Adopted from dead/stalled thread".
            after = _read(state_path) if os.path.exists(state_path) else None
            self.assertEqual(before, after,
                             "a refused adopt must leave STATE.md byte-for-byte unchanged")
            self.assertNotIn("Adopted from dead/stalled thread", after or "")

    def test_adopt_with_the_flag_displaces_the_live_session(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments", "--files", "api/pay.py",
                         "--session", "sessA"], d)
            r = _run_threads(["adopt", "payments", "--adopt-from-live-session"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_record(d, "payments")["state"], "adopted")

    def test_calibrated_gate3_reinjecting_deliver_then_transition_corrupts_state_md(self):
        # CALIBRATION, updated for LOOP P12. The GATE 3 defect was ORDER:
        # deliver the handover, THEN attempt the transition, so a REFUSED
        # adopt still permanently recorded a LIVE thread as adopted. The fix
        # then was to reorder; the fix NOW is that there is no second step to
        # order at all (the handover row is written inside the transition).
        # This reinjects the old two-step, deliver-first shape onto the real
        # product symbol threads_mod._adopt_core, in-process, and confirms the
        # SAME refused adopt still produces a handover for an adoption that
        # never happened. Written against the store rather than STATE.md,
        # because the store is where handover truth lives now.
        def _old_deliver_then_transition(store, root, rec, session_id, adopt_live, name):
            luid = rec["lifecycle_uuid"]
            # Step one, on its own: a handover for an adoption nobody approved.
            with store._transaction():
                store._insert_handover(
                    luid, None,
                    "Adopted from dead/stalled thread: %s" % threads_mod._protect(name))
            # Step two, which is the one that gets refused.
            return store.transition(luid, rec["version"], "adopted",
                                     session_id=session_id, note="adopted by the chief",
                                     adopt_from_live_session=adopt_live)

        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments", "--files", "api/pay.py",
                         "--session", "sessA"], d)
            original = threads_mod._adopt_core
            threads_mod._adopt_core = _old_deliver_then_transition
            try:
                code, printed = _call_thread_cmd_in_process(d, threads_mod.cmd_adopt, ["payments"])
            finally:
                threads_mod._adopt_core = original
            self.assertEqual(code, 2, printed)
            store = bs.Store(d, create=False)
            try:
                orphans = bs._exec(store, "SELECT * FROM handovers").fetchall()
            finally:
                store.close()
            self.assertEqual(
                len(orphans), 1,
                "REINJECTION CHECK: with the old deliver-then-transition shape "
                "restored, a REFUSED adopt must still leave a handover for an "
                "adoption that never committed, reproducing GATE 3; if this "
                "assertion fails, the test above is not calibrated to the defect "
                "it claims to catch")
            self.assertIsNone(orphans[0]["transition_id"],
                              "the orphan is exactly the shape the schema now forbids: "
                              "a handover with no transition behind it")
            rec = _record(d, "payments", states=("active",))
            self.assertIsNotNone(rec, "only the handover should have leaked, "
                                 "not the record's own state")


class TestSecondSessionCannotTakeOver(unittest.TestCase):
    """THE reason this rewire exists: a second session must be REFUSED by
    name, never silently handed the fence a first session already holds. V1
    had no test for this because V1 had no defense against it (F3: a
    reusable name as identity, silent takeover on re-claim)."""

    def test_second_session_refused_on_live_name(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            r1 = _run_threads(["start", "payments", "build payments",
                               "--files", "api/pay.py", "--session", "sessA"], d)
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            r2 = _run_threads(["start", "payments", "steal it",
                               "--files", "api/pay.py", "--session", "sessB"], d)
            self.assertEqual(r2.returncode, 2,
                             "a second session must be refused, not silently granted")
            self.assertIn("refused (name-active)", r2.stdout)
            rec = _record(d, "payments", states=("active",))
            self.assertIsNotNone(rec)
            self.assertEqual(rec["session_id"], "sessA",
                             "ownership must still belong to the first session")
            self.assertEqual(rec["objective"], "build payments",
                             "the second session's objective must never have landed")


class TestParkResumeComplete(unittest.TestCase):
    """resume/park/complete are the store verbs V1 never had."""

    def test_park_resume_complete_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "alpha", "obj", "--files", "api/a.py", "--session", "s1"], d)
            r = _run_threads(["park", "alpha", "--session", "s1"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_record(d, "alpha")["state"], "parked")
            r = _run_threads(["resume", "alpha", "--session", "s1"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_record(d, "alpha")["state"], "active")
            _run_threads(["checkpoint", "alpha", "--next", "ready"], d)
            r = _run_threads(["complete", "alpha", "--session", "s1",
                              "--evidence", "tests pass: 12/12"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_record(d, "alpha")["state"], "complete")

    def test_park_refused_for_the_wrong_session(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "alpha", "obj", "--files", "api/a.py", "--session", "s1"], d)
            r = _run_threads(["park", "alpha", "--session", "s2"], d)
            self.assertEqual(r.returncode, 2)
            self.assertIn("refused", r.stdout)
            self.assertEqual(_record(d, "alpha")["state"], "active",
                             "the wrong session must never be able to park someone else's thread")

    def test_blocker2_resume_from_a_different_session_succeeds_after_off(self):
        # THE founder's ratified hard requirement: thread mode can be
        # switched off mid-project with every thread resumable. Reproduced:
        # monday `off` said "resumable"; tuesday `resume` from a DIFFERENT
        # session was refused not-owner, and the owning session id appeared
        # nowhere a human could see it.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments", "--files", "api/pay.py",
                         "--session", "monday"], d)
            r_off = _run_threads(["off"], d)
            self.assertEqual(r_off.returncode, 0, r_off.stdout + r_off.stderr)
            self.assertIn("resumable", r_off.stdout)
            parked = _record(d, "payments", states=("parked",))
            self.assertIsNotNone(parked)
            self.assertEqual(parked["session_id"], "monday")
            r_resume = _run_threads(["resume", "payments", "--session", "tuesday"], d)
            self.assertEqual(r_resume.returncode, 0, r_resume.stdout + r_resume.stderr)
            resumed = _record(d, "payments", states=("active",))
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed["session_id"], "tuesday",
                             "the resuming session must become the new owner in the "
                             "same transition")
            # The owning session must be discoverable without dumping the DB.
            r_dash = _run_threads(["dashboard"], d)
            self.assertIn("tuesday", r_dash.stdout)


class TestStrictMode(unittest.TestCase):
    def test_strict_exits_nonzero_on_fail(self):
        # A vault with an active session but no session log forces a FAIL check.
        import datetime
        with tempfile.TemporaryDirectory() as v:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            io.open(os.path.join(teld, "outcomes.jsonl"), "w").write(
                json.dumps({"schema": 2, "ts": now, "session_id": "x", "project": "p",
                            "tool_calls": 5, "api_msgs": 9}) + "\n")
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            score = os.path.join(HERE, "bm_score.py")
            strict = subprocess.run([sys.executable, score, "--strict"], env=env,
                                    capture_output=True, text=True)
            advisory = subprocess.run([sys.executable, score], env=env,
                                      capture_output=True, text=True)
            self.assertEqual(strict.returncode, 1, "--strict must exit nonzero on a FAIL")
            self.assertEqual(advisory.returncode, 0, "advisory mode must never block (exit 0)")

    def test_strict_exits_nonzero_when_the_checker_itself_crashes(self):
        """A crashed checker verified NOTHING, so it must not report a pass.

        The top-level handler caught every exception and exited 0 even under
        --strict, which made the CI gate worthless: any bug inside bm_score
        turned into a green build. Local runs still degrade quietly, because
        never-block is a promise to the session, not to CI."""
        with tempfile.TemporaryDirectory() as d:
            broken = os.path.join(d, "bm_score.py")
            src = io.open(os.path.join(HERE, "bm_score.py"), encoding="utf-8").read()
            src = src.replace("def main():",
                              "def main():\n    raise RuntimeError('simulated checker crash')", 1)
            io.open(broken, "w", encoding="utf-8").write(src)
            # PYTHONPATH must reach the real tools dir: bm_score imports
            # bm_telemetry as a sibling, and without this the copy dies at
            # IMPORT time with exit 1, which would make this test pass for the
            # wrong reason and prove nothing about the handler under test.
            env = dict(os.environ, BROTHERMODE_VAULT=os.path.join(d, "vault"),
                       PYTHONPATH=HERE)
            strict = subprocess.run([sys.executable, broken, "--strict"], env=env,
                                    capture_output=True, text=True)
            advisory = subprocess.run([sys.executable, broken], env=env,
                                      capture_output=True, text=True)
            self.assertEqual(strict.returncode, 1,
                             "a checker that crashed must FAIL the CI gate, not pass it")
            self.assertEqual(advisory.returncode, 0,
                             "a local advisory run must still never block")


class TestProjectSecurityClaims(unittest.TestCase):
    """Two project-wide gates that happen to have originally lived inside a
    registry-focused test class; they are not about the registry at all
    (they scan every module under tools/), so they moved here rather than
    being deleted with the class around them."""

    def test_security_md_line_count_claim_is_still_true(self):
        """SECURITY.md tells the reader how much code they have to audit, and
        publishes the command that produces the figure. That number rotted twice
        in one day, which makes the document contradict its own command: worse
        than saying nothing in a file whose whole point is verifiability. So the
        claim is gated rather than trusted. Tolerance is wide on purpose; this
        catches drift, it does not police every commit."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sec = os.path.join(root, "SECURITY.md")
        if not os.path.exists(sec):
            self.skipTest("SECURITY.md not present")
        text = io.open(sec, encoding="utf-8").read()
        m = re.search(r"tools are about ([\d,]+) lines", text)
        self.assertIsNotNone(m, "SECURITY.md no longer states a line count to check")
        claimed = int(m.group(1).replace(",", ""))
        actual = 0
        for dirpath, _dirs, names in os.walk(os.path.join(root, "tools")):
            for n in names:
                if n.endswith(".py") or n.endswith(".sh"):
                    with io.open(os.path.join(dirpath, n), encoding="utf-8", errors="replace") as f:
                        actual += sum(1 for _ in f)
        drift = abs(actual - claimed) / float(max(actual, 1))
        self.assertLess(drift, 0.15,
                        "SECURITY.md claims about %d lines but the tools are %d. "
                        "Update the figure in SECURITY.md." % (claimed, actual))

    def test_no_network_claim_is_mechanically_true(self):
        """SECURITY.md's headline privacy claim ("makes no network calls") was
        published with a grep the reader was expected to run by hand, which
        means the claim rotted the moment nobody ran it. The line-count claim
        beside it has been gated since it drifted twice in one day; the claim
        that actually protects the founder's data was not. It is now.

        Shipping modules only: the test files themselves may import subprocess
        to drive the CLI they are testing, which is local execution, not a
        network call, and is what makes the CLI testable at all."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tools = os.path.join(root, "tools")
        banned = ("urllib", "http", "socket", "requests", "ftplib", "smtplib",
                  "telnetlib", "xmlrpc", "subprocess", "asyncio")
        pattern = re.compile(
            r"^\s*(?:import|from)\s+(%s)\b" % "|".join(banned))
        offenders = []
        # ONE named exception, and it is documented in SECURITY.md rather than
        # hidden here: bm_autosave.py drives git, which is an external binary, so
        # it must import subprocess. Every call it makes is local (no remote, no
        # push, enforced by the shell-and-git half of this test below plus its own
        # suite). The exception is per FILE and per MODULE NAME, so a second file
        # cannot quietly inherit it, and subprocess remains banned everywhere else.
        allowed = {
                   # bm_bench.py (2026-08-10) is the ONE entry here that is
                   # not local-only, and it is written out rather than hidden
                   # among the others. The outcome benchmark invokes an AI
                   # coding agent as a subprocess, and that agent absolutely
                   # does make network calls. So SECURITY.md's headline claim
                   # is scoped rather than absolute: the hooks and tools that
                   # run on their own make no network calls, and this one
                   # tool, which a person runs deliberately and never a hook,
                   # causes traffic by design because measuring an agent
                   # requires running one. Saying "local only" here to keep
                   # the sentence tidy would be exactly the overclaim this
                   # test exists to prevent.
                   "bm_bench.py": {"subprocess"},
                   "bm_autosave.py": {"subprocess"},
                   # bm_reconcile_worktrees.py and bm_release_invariant.py
                   # (2026-08-24) both drive GIT and nothing else. Local only,
                   # read only, and named per file so neither exception can be
                   # inherited by a third tool that happens to import
                   # subprocess for another reason.
                   #
                   # Both exist BECAUSE git is the source of truth for the
                   # question they answer, and neither could be written without
                   # it: one asks whether a worktree's file content is already
                   # reachable from the main branch's history, the other asks
                   # whether a distributable file changed in a commit range.
                   # Reimplementing either without git would mean guessing at
                   # what git already knows, which is the overclaim this test
                   # exists to prevent, one level down.
                   #
                   # Neither takes a remote, fetches, or pushes: they run
                   # rev-parse, diff, cat-file and worktree list against the
                   # local object database.
                   "bm_reconcile_worktrees.py": {"subprocess"},
                   "bm_release_invariant.py": {"subprocess"},
                   # bm_controller.py (L03) runs each unit's deterministic
                   # done-check as a LOCAL command through subprocess, the same
                   # local-only posture as bm_autosave driving git. It makes no
                   # network call; SECURITY.md documents this beside the git one.
                   "bm_controller.py": {"subprocess"},
                   # bm_vault_cli.py (VB7-01, the vault front door) routes
                   # each verb to the sibling bm_vault_*.py file that already
                   # implements it, over THIS SAME interpreter, as a local
                   # subprocess: the same local-only posture as
                   # bm_controller.py running a done-check. It makes no
                   # network call.
                   "bm_vault_cli.py": {"subprocess"},
                   # bm_vault_contract.py (VB10-02) asks git ONE local
                   # question, `git -C <vault> diff --cached --name-only`,
                   # to learn which staged notes the per-class contract
                   # must gate: a local index read, no remote, no push,
                   # the same posture as bm_reconcile_worktrees.py above.
                   "bm_vault_contract.py": {"subprocess"},
                   # bm_vault_lint.py (2026-09-04) derives a note's missing
                   # created field from ONE local git question, `git log
                   # --diff-filter=A --follow --format=%ad --date=short --
                   # <rel>` run inside the vault, to find the commit that
                   # added the note: the same local index read as
                   # bm_vault_contract.py above, no remote, no push, no
                   # socket. id is a sha256 of the note's own path and never
                   # touches subprocess at all.
                   "bm_vault_lint.py": {"subprocess"},
                   # bm_vault_exchange.py (VB8-02) orchestrates the `age`
                   # binary as a local subprocess because rolling age's
                   # crypto in Python would be homemade crypto, which the
                   # module's own docstring refuses. Local pipes only; the
                   # relay repository is driven by git the same local way.
                   "bm_vault_exchange.py": {"subprocess"},
                   # bm_vault_tiers.py (VB10-01) reads the staged diff
                   # (`git -C <vault> diff --cached`, a local index read)
                   # and runs the sibling catalog checker over THIS SAME
                   # interpreter, the bm_vault_cli.py posture. No remote,
                   # no push, no socket.
                   "bm_vault_tiers.py": {"subprocess"},
                   # brothermode_cli.py (v3 Lane B) dispatches the ten public
                   # verbs to the existing local tools through subprocess, and
                   # its update check runs ONE read-only `git ls-remote
                   # --tags` against the configured remote: a network READ,
                   # never a write, never a push, documented in SECURITY.md
                   # beside the other two exceptions.
                   "brothermode_cli.py": {"subprocess"},
                   # bm_continue.py (Phase C, the continuity protocol)
                   # starts the SUCCESSOR SESSION: one local `claude -p`
                   # process, detached, with its output going to a local
                   # log file. Local execution, the same posture as
                   # bm_autosave driving git; it opens no socket and
                   # reaches no remote, and its one network-shaped
                   # neighbour (the update check) lives in
                   # brothermode_cli.py, not here. SECURITY.md documents
                   # this beside the other three.
                   "bm_continue.py": {"subprocess"},
                   # THREE MORE, 2026-08-29, each READ before it was allowed
                   # rather than added to clear a red gate. They landed with
                   # the W9 controller train and the freshness half of the
                   # consolidation work, and were missing from every one of
                   # the four registries at once.
                   #
                   # bm_freshness.py asks git one local question
                   # (`rev-parse --show-toplevel`) and then runs a local
                   # `grep -rlF` over a root directory to decide whether a
                   # cited anchor still resolves. No remote, no push, no
                   # fetch, and a 20 second timeout on the grep.
                   "bm_freshness.py": {"subprocess"},
                   # bm_repair.py starts THIS interpreter against the sibling
                   # tools/bm_vault.py (`recall --query`), exactly the pattern
                   # vault_recall_hook.py and bm_vault_distill.py already carry
                   # above. It shells rather than importing because that
                   # module's search is private, and it returns empty on
                   # failure so losing the lesson never loses the repair.
                   "bm_repair.py": {"subprocess"},
                   # bm_worker_spawn.py starts a CONFIGURED argv as a local
                   # process with a sanitised environment. Stated plainly
                   # rather than implied: like bm_continue.py above, what it
                   # spawns is whatever it was configured to spawn, so if that
                   # is an AI agent then that agent makes network calls. The
                   # tool itself opens no socket, and the same scoping the
                   # bm_bench.py entry uses applies here.
                   "bm_worker_spawn.py": {"subprocess"},
                   # bm_passport.py (the change-passport producer) runs
                   # exactly one command, `git -C <root> config user.name`,
                   # to answer the accountable-person field from a real
                   # source rather than guessing it. A local config read:
                   # no remote, no push, no fetch, and the shell half of
                   # this same test below still bans every network verb.
                   # It is here rather than reading ~/.gitconfig by hand
                   # because git's own precedence (system, global,
                   # conditional includes, repo-local) is the answer, and
                   # reimplementing that parser to dodge an import would
                   # be a worse trade than one named exception.
                   # SECURITY.md documents this beside the other four.
                   "bm_passport.py": {"subprocess"},
                   # bm_bbstatus.py (2026-08-17, the two-host law) is the
                   # ONLY entry in this table that names urllib, and it is
                   # written out rather than folded in with the subprocess
                   # ones. It is the reporting arm of
                   # scripts/local-gates.sh: after a battery finishes, it
                   # POSTs one build status to Bitbucket Cloud, which is a
                   # network WRITE and the only one in this project. It is
                   # never wired into a hook, never runs on its own, and is
                   # reached exactly once per deliberate `local-gates.sh`
                   # invocation on a checkout whose origin is Bitbucket.
                   # The exemption is per FILE and per MODULE NAME like
                   # every other, so no second file inherits it, and
                   # SECURITY.md carries the same sentence rather than
                   # leaving this table the only place it is admitted.
                   "bm_bbstatus.py": {"urllib"},
                   # bm_sessionstart.py and bm_hookchain.py (2026-08-17,
                   # the Windows hook port) drive SIBLING TOOLS as local
                   # processes, which is what the `sh` wrapper they
                   # replaced did. Both were POSIX shell until this change
                   # and therefore invisible to this Python-only check
                   # while they held exactly the same power; the port
                   # brings them INSIDE the audited set rather than
                   # leaving them outside it. Local execution only, the
                   # same posture as bm_autosave.py driving git: no
                   # remote, no network, and every program either starts
                   # is a file in this repository's own tools directory.
                   "bm_sessionstart.py": {"subprocess"},
                   "bm_hookchain.py": {"subprocess"},
                   # bm_fence_hook.py (the battery fence, 2026-08-17) asks
                   # git two local questions while a gate lock is live: `git
                   # ls-files --error-unmatch` for whether a target is
                   # tracked, and nothing else. Local read, no remote, no
                   # push; the same reasoning as bm_passport above: git's
                   # own index is the answer to trackedness, and a private
                   # reimplementation of the index format to dodge an
                   # import would be the worse trade. SECURITY.md documents
                   # this beside the other five.
                   "bm_fence_hook.py": {"subprocess"},
                   # bm_reconcile.py (2026-08-23, startup reconciliation)
                   # asks git two local questions to locate the checkout it
                   # is classifying: `rev-parse --show-toplevel` and
                   # `rev-parse --abbrev-ref`. Local reads only, no remote,
                   # no push, no network verb, and both calls carry explicit
                   # failure paths (127 on OSError, 124 on timeout) rather
                   # than a discarded exit code. Same reasoning as
                   # bm_fence_hook and bm_passport above: git is the answer
                   # to where a checkout starts, and reimplementing that
                   # discovery to dodge an import would be the worse trade.
                   # Granted by the founder on 2026-08-23 after the gate
                   # refused the merge commit. SECURITY.md carries the same
                   # sentence rather than leaving this table its only record.
                   "bm_reconcile.py": {"subprocess"},
                   # Cursor compatibility mode: bm_cursor.py runs git
                   # worktree and founder done-checks; bm_cursor_hook.py
                   # stays subprocess-free (adapter only).
                   "bm_cursor.py": {"subprocess"},
                   # The vault memory tooling, 2026-08-28. The first three
                   # drive LOCAL git plumbing or a local interpreter and
                   # nothing else: bm_private_scan runs symbolic-ref,
                   # rev-list, cat-file, log, for-each-ref and rev-parse;
                   # bm_vault_catalog runs one ls-tree against HEAD;
                   # vault_recall_hook runs this interpreter against a
                   # sibling tool. None of them names fetch, push, pull,
                   # clone or ls-remote, and reading refs/remotes/origin/HEAD
                   # is a local file read, not a network call.
                   "bm_private_scan.py": {"subprocess"},
                   "bm_vault_catalog.py": {"subprocess"},
                   "vault_recall_hook.py": {"subprocess"},
                   # bm_vault_distill.py (2026-08-29, the memory ceremony)
                   # runs this same interpreter against the sibling
                   # bm_vault.py, exactly the vault_recall_hook.py pattern
                   # right above: `index` to refresh, `recall --query` to
                   # search before writing. No remote, no push, no fetch.
                   "bm_vault_distill.py": {"subprocess"},
                   # bm_vault.py is the SECOND entry in this table that is
                   # not local-only, and like bm_bench.py above it is
                   # written out rather than hidden among the others. Its
                   # dense retrieval stage runs tools/bm-embed-bge as a
                   # subprocess, and that shim loads a sentence-transformers
                   # model which DOWNLOADS ITSELF FROM HUGGINGFACE ON FIRST
                   # RUN. Every run after is local, but the first is not.
                   #
                   # Why this is nonetheless not a hole in the headline
                   # claim, and the reason is mechanical rather than a
                   # promise: _embed_bin() returns None when no embedder is
                   # present, and the only shim that reaches one is
                   # tools/bm-embed-bge, which is classified REPO_ONLY in
                   # the packaging suite because it execs an interpreter
                   # inside a .venv-embed built by hand. An installed user
                   # receives no such shim and no such environment, so this
                   # path cannot fire for them at all. It fires only on a
                   # checkout where somebody built that environment on
                   # purpose, and there it downloads once.
                   "bm_vault.py": {"subprocess"},
                   # bm_connectors.py (2026-08-30, the MCP connector
                   # catalog) is the THIRD entry that is not local-only,
                   # written out like bm_bench.py and bm_vault.py above.
                   # Its `check` subcommand probes reachability with `gh
                   # auth status`, one `gh api rate_limit` call, `ssh -T
                   # git@bitbucket.org` and `az account show`, which are
                   # network READS made on purpose: a reachability check
                   # that touched nothing would be theater. It is never
                   # wired into a hook, runs only on explicit invocation,
                   # reads no credential value, and its print-add path
                   # executes nothing, which its own suite proves by
                   # making every subprocess entry point raise.
                   # SECURITY.md carries the same admission beside the
                   # bm_bbstatus.py one.
                   #
                   # 2026-08-30, same file, one more import: `urllib`, for
                   # the Azurite probe added beside the az CLI check. It
                   # makes one unauthenticated GET to 127.0.0.1:10000 (the
                   # loopback address, never a remote host) to see whether
                   # a local Azurite storage emulator answers and to read
                   # its "Server: Azurite-Blob" header off an ordinary
                   # HTTPError; it never signs a request with the
                   # well-known devstoreaccount1 key, so it reaches no
                   # data, real or synthetic. A silent port is NO-DATA
                   # naming `npx azurite`, not a failure.
                   "bm_connectors.py": {"subprocess", "urllib"},
                   # bm_vault_posture.py (2026-08-30, WBS row VB8-01, the
                   # encryption posture census) runs `diskutil apfs list`
                   # and `df -P <path>`, two local, read-only OS utilities,
                   # to read the operating system's own answer for whether
                   # the volume holding the vault (and each derived store)
                   # is encrypted at rest. Neither takes a remote argument.
                   # SECURITY.md carries the same admission.
                   "bm_vault_posture.py": {"subprocess"},
                   # bm_vault_serve.py (2026-08-30, VB2-02: the vault
                   # answers over the wire) is the FOURTH entry that is
                   # not local-only, and unlike the others above it opens
                   # an actual socket: it is a dependency-free HTTP server
                   # (http.server) fronting the existing recall stack,
                   # invoked to subprocess tools/bm_vault.py recall for
                   # every POST /recall. Binds 127.0.0.1 by default and
                   # REFUSES to start on any other interface without a
                   # token file (a shared secret read from a file, never
                   # argv, checked with secrets.compare_digest); it is
                   # never wired into a hook and only runs when a person
                   # starts it deliberately. SECURITY.md carries the same
                   # admission.
                   "bm_vault_serve.py": {"subprocess", "http"},
                   # bm_vault_pane.py (2026-08-30, WBS row VB11-05: the
                   # approval pane) is the FIFTH entry that is not
                   # local-only, and like bm_vault_serve.py it opens an
                   # actual socket via http.server, extending that same
                   # module's own pattern rather than a second one: binds
                   # 127.0.0.1 by default and REFUSES to start on any other
                   # interface without a token file, is never wired into a
                   # hook, and only runs when a person starts it
                   # deliberately. Unlike bm_vault_serve.py it never
                   # subprocesses anything: every Approve/Reject click runs
                   # the real promotion or curation command as a direct
                   # function call, so no "subprocess" entry is needed here.
                   # SECURITY.md carries the same admission.
                   "bm_vault_pane.py": {"http"},
                   # bm_vault_notify.py (2026-08-30, WBS row VB11-06, the
                   # notification delivery lane) is the FIFTH entry that is
                   # not local-only, and it carries the FIFTH network-shaped
                   # import name in this table, smtplib, written out rather
                   # than folded in with the local subprocess ones. Its
                   # `send --channel email` adapter makes one deliberate SMTP
                   # connection on EXPLICIT invocation only, never from a
                   # hook, never automatically: a founder places a mailbox
                   # credential in the macOS keychain by hand, and only then
                   # does a send reach an SMTP server. No credential means
                   # NO-DATA, naming the keychain item, before smtplib is
                   # ever touched. `subprocess` is also named here, for the
                   # ONE local, no-network `security find-generic-password`
                   # call that reads that same credential at call time; like
                   # bm_passport.py and bm_fence_hook.py above, a local
                   # keychain read has no substitute that avoids the local
                   # binary. The Teams adapter opens no socket at all: it is
                   # fixture-mode only until tenant consent (docs/
                   # TEAMS-CONSENT-REQUEST.md), and writes its payload to a
                   # caller-named --mock-sink file instead of calling Graph.
                   "bm_vault_notify.py": {"subprocess", "smtplib"},
                   # bm_embed_warm.py (2026-08-31, WBS row VB5-06, the warm
                   # embedder) is the SIXTH entry that is not local-only,
                   # and like bm_vault_serve.py and bm_vault_pane.py it
                   # opens an actual socket via http.server (its `serve`
                   # daemon) and http.client (the dense path's client
                   # seam, embed_via_warm): binds 127.0.0.1 by default and
                   # REFUSES to start on any other interface without a
                   # token file, is never wired into a hook, and only runs
                   # when a person starts it deliberately. `subprocess` is
                   # named here too, for the `measure` subcommand's own
                   # child process (a short-lived copy of this same daemon,
                   # started only to be timed and torn down) and for its
                   # comparison call into bm_vault.py's original subprocess
                   # embed path, unchanged local execution, no remote, no
                   # push, no fetch. SECURITY.md carries the same
                   # admission.
                   "bm_embed_warm.py": {"subprocess", "http"},
                   # vault_client.py (2026-08-31, WBS row VB3-09, the service
                   # contract) is the SEVENTH entry that is not local-only:
                   # a minimal stdlib HTTP CLIENT for bm_vault_serve.py's own
                   # recall and health routes, meant to be lifted onto a
                   # second machine standalone. Uses urllib.request only,
                   # never opens a listening socket of its own, and connects
                   # only to a base_url the caller names (127.0.0.1 by
                   # default in its own --base-url flag). SECURITY.md
                   # carries the same admission.
                   "vault_client.py": {"urllib"}}
        for n in sorted(os.listdir(tools)):
            if not n.endswith(".py") or n.startswith("test_"):
                continue
            for i, line in enumerate(
                    io.open(os.path.join(tools, n), encoding="utf-8"), 1):
                m = pattern.match(line)
                if m and m.group(1) not in allowed.get(n, ()):
                    offenders.append("%s:%d imports %s" % (n, i, m.group(1)))
        self.assertEqual(
            offenders, [],
            "SECURITY.md claims BrotherMode makes no network calls and that the "
            "tools run no subprocess, but %s. Either remove the import or "
            "correct SECURITY.md; do not leave the document claiming more than "
            "the code delivers." % ", ".join(offenders))

        # The shell half of the claim: the autosave runs git locally and must
        # never reach a remote. A pushing autosave would send the founder's
        # entire working tree, untracked files included, somewhere it was never
        # promised to go.
        net_cmds = re.compile(r"(?<![\w-])(curl|wget|nc|ssh|scp)\s")
        push = re.compile(r"git\s+(?:-C\s+\S+\s+)?(?:push|fetch|pull|clone|remote)\b")
        shell_offenders = []
        for n in sorted(os.listdir(tools)):
            if not n.endswith(".sh"):
                continue
            for i, line in enumerate(
                    io.open(os.path.join(tools, n), encoding="utf-8"), 1):
                code = line.split("#", 1)[0]
                if net_cmds.search(code) or push.search(code):
                    shell_offenders.append("%s:%d" % (n, i))
        self.assertEqual(
            shell_offenders, [],
            "a shell tool now reaches the network or a git remote (%s). "
            "SECURITY.md promises the autosave is local only and never pushes."
            % ", ".join(shell_offenders))


class TestThreadsUseStore(unittest.TestCase):
    """Ported from TestThreadsUseRegistry: the same behavioural claims,
    re-verified against the store the CLI now actually writes to. The
    cross-record topic-clash test that used to live here is deleted, not
    ported: bm_store.decide() is a per-lifecycle append-only primitive with
    no cross-record awareness, and the ratified command mapping does not
    name clash detection as a store concern. This is a real feature
    reduction, reported in the implementer's report, not an oversight."""

    def _run(self, cwd, *a):
        return _run_threads(a, cwd)

    def test_start_registers_a_record_with_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "build payments", "--files", "api/pay.py")
            rec = _record(d, "payments", states=("active",))
            self.assertIsNotNone(rec, "start did not create a work record")
            self.assertEqual(_claims(d, rec["lifecycle_uuid"]), ["api/pay.py"])

    def test_start_refuses_an_overlapping_thread(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "pay", "--files", "api/pay.py")
            r = self._run(d, "start", "billing", "bill", "--files", "api/pay.py")
            self.assertEqual(r.returncode, 2)
            self.assertIn("refused (overlap)", r.stdout)
            self.assertIn("payments", r.stdout, "the refusal must name the conflicting record")

    def test_off_absorbs_through_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(d, "on")
            self._run(d, "start", "payments", "pay", "--files", "api/pay.py")
            self._run(d, "checkpoint", "payments", "--topic", "api",
                      "--decision", "chose Stripe", "--next", "wire webhook")
            self._run(d, "off")
            state = io.open(os.path.join(d, "STATE.md")).read()
            self.assertIn("chose Stripe", state)
            self.assertIn("wire webhook", state)
            self.assertEqual(_record(d, "payments")["state"], "parked")


class TestPreWriteGate(unittest.TestCase):
    """Every place in tools/, scripts/, mcp/ and brotherme/ that writes,
    replaces, copies, removes or chmods a file must be a REVIEWED place.
    Proves a new write site cannot appear unreviewed, the gap that let a
    secret leak ship three times in one week.

    Widened for C-04: old scanner matched only open(...'w'...), os.open( and
    .write(), only inside tools/. Missed os.replace, shutil's write
    functions, os.mkdir/makedirs, os.unlink/remove and os.chmod inside files
    already reviewed, and never scanned scripts/, mcp/, brotherme/.

    Widened again for M27: the mode pattern required an exact single "w"
    between quotes, so open(p, "wb") (or "ab", "xb", "w+" and similar)
    never matched at all; the write-call pattern matched only .write(, so
    .writelines( and shutil.copyfileobj( were invisible even when the mode
    pattern above also missed them. A site written as open(p, "wb")
    followed by writelines or copyfileobj was invisible in both directions
    at once. NOT a live hole when this landed: the one binary open in the
    tree (tools/bm_handover.py's zip_path write) already sat on a line
    followed by a plain fh.write(data), which the old .write( pattern
    already matched, so that site was already counted; this closes a
    latent gap, not a bleeding one. The mode class now accepts any mode
    string starting with w, a or x (write, append, exclusive-create) followed
    by up to three more mode characters (b, t, +, or a repeat of r/w/x/a),
    which also covers os.fdopen(fd, "wb") and friends since fdopen( shares
    the "open(" substring the pattern searches for. Deliberately NOT
    widened to match a leading "r": open(p, "r") and open(p, "rb") are the
    single most common call shape in this tree, and matching reads would
    make the scanner "match everything", which is as useless as matching
    nothing; "r+" (update mode, technically write-capable) is left out for
    the same reason and because grepping this tree found zero uses of it.
    os.fdopen( also gained its own unconditional pattern, the same
    catch-all treatment os.open( already had, so a future fdopen call whose
    mode is a variable rather than a literal string still cannot hide.

    Widened again for M28: the gap between "open(" and the mode string was
    [^)]*, which cannot cross a NESTED closing paren, so a path argument
    built from a call like os.path.join(...) hid the mode string behind
    that call's own ")" and the whole open(...) site went unmatched, real
    shape at scripts/benchmark.py line 116 (with open(os.path.join(self.
    root, ".git", "info", "exclude"), "a") as fh:). Latent there only
    because a matched .write( sits two lines below it. That widening
    accepted one BALANCED, ONE-LEVEL-DEEP parenthesized group, a bounded
    patch on a regex, not a fix.

    D4 (M29): a regex reads spelling, not meaning, so it can never see a
    file handle handed to a function that writes it, like
    json.dump(obj, fh) inside a with open(...) as fh: block. Verified
    2026-08-21: the nine-pattern regex tuple matched none of it. That was
    the third generation of the same blind spot in one night (M27 the mode
    string, M28 the nested paren, M29 the write verb), and the finding
    that mattered more than any one patch: a list of spellings cannot
    answer "does this line write a file", because the answer depends on
    what a NAME is bound to, not on what characters sit on its line.

    So the scanner now walks the AST instead of grepping lines. It tracks
    every name bound by `with open(...) as x` (any number of items in one
    with, including io.open and os.fdopen) and by `x = open(...)`, in the
    write-capable modes this gate has always cared about (w/a/x, optionally
    followed by up to three of b/t/+/r/w/x/a; the class docstring's older
    reasoning against matching bare reads still holds, so mode-less or
    read-mode opens are not tracked as write handles). Any call that later
    receives one of those tracked names as a positional or keyword
    argument is flagged, whatever the call is named: json.dump(obj, fh),
    pickle.dump(obj, fh), print(x, file=fh), csv.writer(fh), all now
    visible because they reason about the BINDING, not the spelling. The
    direct call shapes the old patterns matched (os.replace, the shutil
    write functions, os.mkdir/makedirs, os.unlink/remove, os.chmod,
    os.open, os.fdopen, and any .write(/.writelines( call) are still
    flagged unconditionally, same coverage as before, now as AST node
    shapes rather than substrings. One flagged line still counts once,
    matching the old scanner's per-line counting exactly.
    """

    #: Calls flagged unconditionally: (receiver name, attribute) for a
    #: dotted call like os.replace(...). Carries forward every semantic
    #: category the old regex list covered besides open( and
    #: .write(/.writelines(, which get their own AST shape checks below
    #: because they are not simple dotted-name calls.
    _DIRECT_WRITE_CALLS = frozenset((
        ("os", "replace"),
        ("os", "mkdir"), ("os", "makedirs"),
        ("os", "unlink"), ("os", "remove"),
        ("os", "chmod"),
        ("os", "open"), ("os", "fdopen"),
        ("shutil", "copy"), ("shutil", "copy2"), ("shutil", "copyfile"),
        ("shutil", "copytree"), ("shutil", "copyfileobj"),
        ("shutil", "move"), ("shutil", "rmtree"),
    ))

    #: A literal open() mode string counts as write-capable if it starts
    #: with w, a or x, same character class the old regex used.
    _WRITE_MODE_RE = re.compile(r'^[wax][rwxabt+]{0,3}$')

    class _WriteSiteVisitor(ast.NodeVisitor):
        """Walks one file's AST, tracking write-capable file handles and
        flagging every line that writes, or hands a tracked handle to
        another call. See the class docstring above for the full design."""

        def __init__(self, direct_calls, write_mode_re):
            self.flagged_lines = set()
            self._direct_calls = direct_calls
            self._write_mode_re = write_mode_re
            self._scopes = [set()]  # stack of tracked-handle-name sets

        # -- scope handling: a nested function sees its own bindings plus
        # every enclosing scope's, matching normal closure lookup. --
        def visit_FunctionDef(self, node):
            self._scopes.append(set())
            self.generic_visit(node)
            self._scopes.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_Lambda = visit_FunctionDef

        def _track(self, name):
            self._scopes[-1].add(name)

        def _is_tracked(self, name):
            return any(name in scope for scope in self._scopes)

        @staticmethod
        def _dotted(func):
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                return (func.value.id, func.attr)
            return None

        def _is_open_call(self, call):
            func = call.func
            if isinstance(func, ast.Name) and func.id == "open":
                return True
            return self._dotted(func) == ("io", "open")

        def _is_fdopen_call(self, call):
            return self._dotted(call.func) == ("os", "fdopen")

        def _literal_mode(self, call):
            for i, arg in enumerate(call.args):
                if i == 1 and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return arg.value
            for kw in call.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    return kw.value.value
            return None

        def _is_write_capable_open(self, call):
            """A mode-less open() defaults to read, so it is deliberately
            NOT write-capable, same posture the class docstring already
            states for why a bare "r" was never matched. A mode given as a
            variable (not a literal) cannot be proven read-only, so it is
            treated as write-capable, the same conservative call os.open/
            os.fdopen already got unconditionally."""
            mode = self._literal_mode(call)
            if mode is not None:
                return bool(self._write_mode_re.match(mode))
            has_mode_arg = len(call.args) > 1 or any(
                kw.arg == "mode" for kw in call.keywords)
            return has_mode_arg

        def _flag(self, node):
            self.flagged_lines.add(node.lineno)

        def _handle_call(self, call):
            dotted = self._dotted(call.func)
            if dotted in self._direct_calls:
                self._flag(call)
            elif isinstance(call.func, ast.Attribute) \
                    and call.func.attr in ("write", "writelines"):
                self._flag(call)
            elif self._is_open_call(call) and self._is_write_capable_open(call):
                self._flag(call)
            else:
                for arg in call.args:
                    if isinstance(arg, ast.Name) and self._is_tracked(arg.id):
                        self._flag(call)
                        break
                else:
                    for kw in call.keywords:
                        if isinstance(kw.value, ast.Name) and self._is_tracked(kw.value.id):
                            self._flag(call)
                            break

        def visit_Call(self, node):
            self._handle_call(node)
            self.generic_visit(node)

        def _bind_if_write_handle(self, value, target):
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
                return
            if self._is_fdopen_call(value):
                self._track(target.id)
            elif self._is_open_call(value) and self._is_write_capable_open(value):
                self._track(target.id)

        def visit_With(self, node):
            for item in node.items:
                self._bind_if_write_handle(item.context_expr, item.optional_vars)
            self.generic_visit(node)

        visit_AsyncWith = visit_With

        def visit_Assign(self, node):
            if len(node.targets) == 1:
                self._bind_if_write_handle(node.value, node.targets[0])
            self.generic_visit(node)

    #: Repo root, one level above tools/.
    ROOT = os.path.dirname(HERE)

    #: Directories this gate is responsible for, relative to ROOT. All four
    #: directories the closure register names for C-04: tools, scripts, mcp,
    #: brotherme.
    SCAN_ROOTS = ("tools", "scripts", "mcp", "brotherme")

    #: Never descended into: caches and build metadata, not reviewable source.
    EXCLUDED_DIRS = {"__pycache__"}

    def _iter_py_files(self, base, root_rel):
        root_abs = os.path.join(base, root_rel)
        for dirpath, dirnames, filenames in os.walk(root_abs):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in self.EXCLUDED_DIRS
                and not d.startswith(".")
                and not d.endswith(".egg-info"))
            for fn in sorted(filenames):
                if fn.endswith(".py") and not fn.startswith("test_"):
                    yield os.path.join(dirpath, fn)

    def _sites(self, base=None, scan_roots=None):
        base = self.ROOT if base is None else base
        scan_roots = self.SCAN_ROOTS if scan_roots is None else scan_roots
        found = {}
        for root_rel in scan_roots:
            for path in self._iter_py_files(base, root_rel):
                key = os.path.relpath(path, base).replace(os.sep, "/")
                with io.open(path, encoding="utf-8") as fh:
                    src = fh.read()
                try:
                    tree = ast.parse(src, filename=path)
                except SyntaxError as exc:
                    # Fail closed: a file this gate cannot parse is a file
                    # it cannot prove clean, so it is never silently
                    # dropped from the count (NO-DATA is not a pass here).
                    raise AssertionError(
                        "%s could not be parsed by the write-site scanner "
                        "(%s); it cannot be verified for write sites" %
                        (key, exc))
                visitor = self._WriteSiteVisitor(
                    self._DIRECT_WRITE_CALLS, self._WRITE_MODE_RE)
                visitor.visit(tree)
                if visitor.flagged_lines:
                    found[key] = len(visitor.flagged_lines)
        return found

    def _assert_matches_manifest(self, actual, manifest):
        """Shared by test_no_unreviewed_write_sites and the adversarial test
        below, so the adversarial test exercises the REAL gate, not a copy."""
        self.assertTrue(actual, "the write-site scanner found nothing; it is broken")
        for key in sorted(manifest):
            self.assertIn(key, actual,
                          "%s is in the reviewed inventory but no longer writes any "
                          "file. If it was renamed or its writes moved, update "
                          "tools/write_sites.json to match." % key)
        for key, count in sorted(actual.items()):
            self.assertIn(key, manifest,
                          "%s writes files but is not in the reviewed inventory. "
                          "Review whether every text it writes passes through "
                          "redaction, then add it to tools/write_sites.json." % key)
            self.assertEqual(
                count, manifest[key],
                "%s has %d write sites but %d were reviewed. A write site was "
                "added or removed: confirm it redacts user or model text, then "
                "update tools/write_sites.json." % (key, count, manifest[key]))

    def test_no_unreviewed_write_sites(self):
        manifest_path = os.path.join(HERE, "write_sites.json")
        self.assertTrue(os.path.exists(manifest_path),
                        "write_sites.json is missing; it is the reviewed inventory")
        with io.open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)["reviewed"]
        actual = self._sites()
        self._assert_matches_manifest(actual, manifest)

    def test_widened_scope_catches_a_smuggled_site(self):
        """C-04 adversarial test. Pre-widening, os.replace and any directory
        outside tools/ were BOTH invisible at once. Plant a throwaway file
        writing only via os.replace inside a directory named "scripts" under
        an isolated temp root, never touching the real repo, and confirm the
        SAME comparison the real gate runs refuses it unreviewed."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            planted_dir = os.path.join(root, "scripts")
            os.makedirs(planted_dir)
            planted = os.path.join(planted_dir, "bm_smuggled_write.py")
            with io.open(planted, "w", encoding="utf-8") as f:
                f.write("import os\n\n\ndef rotate(tmp, path):\n"
                        "    os.replace(tmp, path)\n")

            found = self._sites(base=root, scan_roots=("scripts",))

            self.assertIn("scripts/bm_smuggled_write.py", found,
                          "the widened scanner missed an os.replace site "
                          "inside a scripts-shaped directory; the C-04 fix "
                          "regressed")
            self.assertEqual(found["scripts/bm_smuggled_write.py"], 1)

            with self.assertRaises(AssertionError):
                self._assert_matches_manifest(found, manifest={})

    def test_widened_scope_catches_a_smuggled_binary_write(self):
        """M27 adversarial test, the second widening. Pre-widening, two
        escaping shapes were BOTH invisible at once: open(p, "wb") never
        matched the mode pattern (it required an exact single "w" between
        quotes), and .writelines(/shutil.copyfileobj( never matched the
        write-call pattern (it matched only .write(). Plant a throwaway
        file using exactly those two shapes inside a directory named
        "scripts" under an isolated temp root, never touching the real
        repo, and confirm the SAME comparison the real gate runs both sees
        it and refuses it unreviewed."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            planted_dir = os.path.join(root, "scripts")
            os.makedirs(planted_dir)
            planted = os.path.join(planted_dir, "bm_smuggled_binary_write.py")
            with io.open(planted, "w", encoding="utf-8") as f:
                f.write(
                    "import shutil\n\n\n"
                    "def smuggled_write(tmp, path, src_fh, dst_fh):\n"
                    "    with open(tmp, \"wb\") as fh:\n"
                    "        fh.writelines([b\"data\"])\n"
                    "    shutil.copyfileobj(src_fh, dst_fh)\n")

            found = self._sites(base=root, scan_roots=("scripts",))

            self.assertIn(
                "scripts/bm_smuggled_binary_write.py", found,
                "the widened scanner missed an open(...\"wb\") or a "
                ".writelines()/shutil.copyfileobj() site inside a "
                "scripts-shaped directory; the M27 fix regressed")
            self.assertEqual(found["scripts/bm_smuggled_binary_write.py"], 3)

            with self.assertRaises(AssertionError):
                self._assert_matches_manifest(found, manifest={})

    def test_widened_scope_catches_a_smuggled_nested_paren_write(self):
        """M28 adversarial test, the third widening. Pre-widening, the
        open(...) mode-string pattern reached the mode string through
        [^)]*, which cannot cross a NESTED closing paren: open(os.path.
        join(...), "a") was invisible whenever the mode string sat past a
        nested call in the path argument, exactly the real shape at
        scripts/benchmark.py line 116 (with open(os.path.join(self.root,
        ".git", "info", "exclude"), "a") as fh:). LATENT there only
        because a matched .write( sits two lines below it; this fixture
        has NO write call beside it at all, the estate where the gap goes
        live. Plant a throwaway file using exactly that nested-paren shape
        inside a directory named "scripts" under an isolated temp root,
        never touching the real repo, and confirm the SAME comparison the
        real gate runs both sees it and refuses it unreviewed."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            planted_dir = os.path.join(root, "scripts")
            os.makedirs(planted_dir)
            planted = os.path.join(planted_dir, "bm_smuggled_nested_write.py")
            with io.open(planted, "w", encoding="utf-8") as f:
                f.write(
                    "import os\n\n\n"
                    "def touch(root):\n"
                    "    with open(os.path.join(root, \"state\", \"marker\"), "
                    "\"w\") as fh:\n"
                    "        fh.flush()\n")

            found = self._sites(base=root, scan_roots=("scripts",))

            self.assertIn(
                "scripts/bm_smuggled_nested_write.py", found,
                "the widened scanner missed an open(...) site whose mode "
                "string sits past a NESTED closing paren in the path "
                "argument, with no .write() call beside it to catch the "
                "file some other way; the M28 fix regressed")
            self.assertEqual(found["scripts/bm_smuggled_nested_write.py"], 1)

            with self.assertRaises(AssertionError):
                self._assert_matches_manifest(found, manifest={})

    def test_ast_walk_catches_a_handle_smuggled_into_json_dump(self):
        """D4 (M29) adversarial test, the fourth widening, and the first one
        that a regex could never pass: json.dump(obj, fh) never matched ANY
        generation of WRITE_PATTERNS, verified 2026-08-21 by running the
        live nine-pattern tuple against that exact line. The scanner is now
        an AST walk that tracks the name a write-mode `with open(...) as
        fh:` binds and flags any call fh is later handed to, so this shape
        is caught by reasoning about the binding, not by adding a tenth
        spelling. Plant the real shape from scripts/benchmark_comparative.py
        (an open(...) in "w" mode, immediately followed by json.dump(obj,
        fh) two lines later, no fh.write() anywhere) inside a directory
        named "scripts" under an isolated temp root, never touching the
        real repo, and confirm the SAME comparison the real gate runs both
        sees it and refuses it unreviewed."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            planted_dir = os.path.join(root, "scripts")
            os.makedirs(planted_dir)
            planted = os.path.join(planted_dir, "bm_smuggled_json_dump.py")
            with io.open(planted, "w", encoding="utf-8") as f:
                f.write(
                    "import json\n\n\n"
                    "def save(path, obj):\n"
                    "    with open(path, \"w\") as fh:\n"
                    "        json.dump(obj, fh, indent=2)\n")

            found = self._sites(base=root, scan_roots=("scripts",))

            self.assertIn(
                "scripts/bm_smuggled_json_dump.py", found,
                "the AST walk missed a file handle handed onward to "
                "json.dump(); the D4 (M29) fix regressed")
            # Two flagged lines: the write-mode open( itself, and the
            # json.dump( line that receives the handle it bound.
            self.assertEqual(found["scripts/bm_smuggled_json_dump.py"], 2)

            with self.assertRaises(AssertionError):
                self._assert_matches_manifest(found, manifest={})

    def test_ast_walk_does_not_flag_a_handle_only_ever_read(self):
        """A read-mode handle handed to a reader (json.load(fh)) must stay
        invisible to this gate, on purpose: matching every open() the way
        the mode-only regex never did would make the scanner 'match
        everything', which the class docstring already rejects as useless
        as matching nothing. Only a WRITE-capable handle (mode absent
        means read, so it is excluded) gets tracked, so a bare `with
        open(path) as fh: json.load(fh)` must report zero sites."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            planted_dir = os.path.join(root, "scripts")
            os.makedirs(planted_dir)
            planted = os.path.join(planted_dir, "bm_read_only_handle.py")
            with io.open(planted, "w", encoding="utf-8") as f:
                f.write(
                    "import json\n\n\n"
                    "def load(path):\n"
                    "    with open(path) as fh:\n"
                    "        return json.load(fh)\n")

            found = self._sites(base=root, scan_roots=("scripts",))

            self.assertNotIn(
                "scripts/bm_read_only_handle.py", found,
                "a read-mode handle handed to json.load() was flagged as a "
                "write site; the AST walk must track write-capable opens "
                "only, or every read call in the tree needs reviewing too")


class TestHonestReportingUnderFailure(unittest.TestCase):
    """I6, recalibrated. bm_threads.py now wraps a store where every mutation
    is already its own atomic transaction, so the honesty surface that
    matters at THIS layer is narrower than V1's: does the CLI wrapper ever
    turn a genuine store success into a reported failure, or silently eat a
    local VIEW-file failure (a mailbox regeneration) as if the underlying
    store write had failed too? Each test patches exactly one function in an
    in-process import of bm_threads.py, the same calibration V1's own I6
    suite used: permissions fail the FIRST write in a command and never
    reach the one under test, so the injection has to be this precise."""

    def _mod(self, tag):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_threads_honesty_%s" % tag, os.path.join(HERE, "bm_threads.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @contextlib.contextmanager
    def _in(self, d):
        old = os.getcwd()
        os.chdir(d)
        try:
            yield
        finally:
            os.chdir(old)

    def test_start_reports_failure_when_thread_files_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as d:
            mod = self._mod("start")
            with self._in(d):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.cmd_on([])
                mod._atomic_write = lambda path, text: False
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        mod.cmd_start(["alpha", "obj", "--files", "api/a.py"])
                except SystemExit as e:
                    self.assertEqual(e.code, 2)
                out = buf.getvalue()
            self.assertNotIn("created at", out,
                             "reported the thread created while its files could not be written")
            self.assertIn("START FAILED", out)
            # And honestly: the store record itself must still exist (claim()
            # is its own atomic transaction, a separate boundary from the
            # local mailbox files, and the message says exactly that).
            rec = _record(d, "alpha", states=("active",))
            self.assertIsNotNone(rec, "the store record must not vanish just because "
                                 "the local mailbox files failed to write")

    def test_checkpoint_warns_but_does_not_lie_when_the_digest_view_write_fails(self):
        with tempfile.TemporaryDirectory() as d:
            mod = self._mod("checkpoint")
            with self._in(d):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.cmd_on([])
                    mod.cmd_start(["alpha", "obj", "--files", "api/a.py"])
                mod._atomic_write = lambda path, text: False
                out_buf, err_buf = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                    mod.cmd_checkpoint(["alpha", "--next", "next: LIETOKEN"])
                out, err = out_buf.getvalue(), err_buf.getvalue()
            # The STORE write really did succeed (checkpoint() is its own
            # transaction), so reporting it recorded is honest, not a lie...
            self.assertIn("checkpoint", out)
            self.assertIn("recorded", out)
            # ...but the view that failed to refresh must be named, not silent.
            self.assertIn("digest.md", err)
            digests = json.dumps(_dump(d, raw=True)["digests"])
            self.assertIn("LIETOKEN", digests,
                          "the checkpoint really must be in the store even though the "
                          "local digest.md view failed to refresh")

    def test_off_never_reports_success_when_the_mode_write_fails(self):
        with tempfile.TemporaryDirectory() as d:
            mod = self._mod("off")
            with self._in(d):
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.cmd_on([])
                mod._save_mode = lambda root, d: False
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        mod.cmd_off([])
                except SystemExit:  # sbe: allow-silent tolerates cmd_off's expected SystemExit; the real assertion below reads the OFF-honesty output, not this exit
                    pass
                out = buf.getvalue()
            self.assertNotIn("thread mode OFF (drained", out,
                             "I6: reported OFF while the mode write failed")


class TestPartialOffAtomicity(unittest.TestCase):
    """I8, recalibrated: one thread's handover succeeds, a sibling's fails.
    The failing one must stay ACTIVE and fenced; the succeeding one must
    still be parked, and `off` must say so honestly rather than report every
    record as untouched when some of them really were drained (V1's own
    off-outside-the-lock race produced exactly this shape of lie, measured
    at 28 of 30 trials before it was fixed)."""

    def _mod(self, tag):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_threads_partial_%s" % tag, os.path.join(HERE, "bm_threads.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_a_partial_off_parks_the_survivor_and_holds_the_failure(self):
        with tempfile.TemporaryDirectory() as d:
            mod = self._mod("partial")
            old = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.cmd_on([])
                    mod.cmd_start(["alpha", "a", "--files", "api/a.py"])
                    mod.cmd_start(["beta", "b", "--files", "api/b.py"])
                    mod.cmd_checkpoint(["alpha", "--next", "next: A"])
                    mod.cmd_checkpoint(["beta", "--next", "next: B"])
                # LOOP P12: the injection point moved with the code. The
                # handover is no longer a separate call `off` makes, so the
                # failure is injected where it now lives, inside the store's
                # transaction. Beta's park must therefore roll back WITH its
                # handover, which is a stronger result than before: beta ends
                # with no handover AND no transition, not merely unparked.
                store_mod = mod._store()
                real = store_mod.Store._insert_handover

                def beta_fails(self_store, lifecycle_uuid, transition_id, heading,
                               **kwargs):
                    if "beta" in heading:
                        raise store_mod.OwnershipRefused(
                            "injected-handover-failure",
                            "injected: beta's handover cannot be built")
                    return real(self_store, lifecycle_uuid, transition_id, heading,
                                **kwargs)
                store_mod.Store._insert_handover = beta_fails
                try:
                    with contextlib.redirect_stdout(io.StringIO()), \
                         contextlib.redirect_stderr(io.StringIO()):
                        try:
                            mod.cmd_off([])
                        except SystemExit:  # sbe: allow-silent tolerates cmd_off's expected SystemExit from the injected handover failure; the real assertions read the parked/active state after
                            pass
                finally:
                    store_mod.Store._insert_handover = real
            finally:
                os.chdir(old)
            self.assertEqual(_record(d, "alpha")["state"], "parked",
                             "the sibling whose handover succeeded must still be parked")
            self.assertEqual(_record(d, "beta")["state"], "active",
                             "beta's fence must stay held since its handover failed")
            mode = json.load(io.open(os.path.join(d, "threads", "thread-mode.json")))
            self.assertEqual(mode["mode"], "on",
                             "mode must stay ON while any active persistent record remains")


class TestReclaimPreservesWorkingFiles(unittest.TestCase):
    """I9, recalibrated: V1 allowed re-running `start` on any live thread by
    name alone. V2's claim() only allows an in-place update ("reclaim") when
    the SAME session re-declares the SAME active name; a different session
    is refused (TestSecondSessionCannotTakeOver). This proves the legitimate
    reclaim path still never stamps a blank template over a working plan,
    chief directives, or an advancement history that already exist."""

    def test_restarting_with_the_same_session_preserves_every_working_file(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "alpha", "objective", "--files", "api/a.py",
                         "--session", "s1"], d)
            tdir = [n for n in os.listdir(os.path.join(d, "threads")) if n.startswith("alpha-")][0]
            base = os.path.join(d, "threads", tdir)
            marks = {"STATE.md": "MY-WORKING-PLAN", "inbox.md": "CHIEF-DIRECTIVE",
                     "outbox.md": "ADVANCEMENT-LOG", "digest.md": "HANDOVER-CONTENT"}
            before = {}
            for fname, mark in marks.items():
                with io.open(os.path.join(base, fname), "a", encoding="utf-8") as fh:
                    fh.write("\n%s\n" % mark)
                before[fname] = io.open(os.path.join(base, fname), encoding="utf-8").read()
            r = _run_threads(["start", "alpha", "different objective", "--files", "api/a.py",
                              "--session", "s1"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for fname, mark in marks.items():
                after = io.open(os.path.join(base, fname), encoding="utf-8").read()
                self.assertEqual(after, before[fname],
                                 "reclaiming 'alpha' changed %s and lost %s" % (fname, mark))


class TestArchitecturalGuards(unittest.TestCase):
    """Structural checks that stay true by construction, not by discipline
    someone has to remember."""

    def test_bm_threads_has_no_bm_registry_reference(self):
        # Scoped to this rewire's own fence (bm_threads.py, and bm_registry.py's
        # deletion): bm_store.py is OUTSIDE that fence and is deliberately not
        # asserted on here. bm_store.py's own module docstring names
        # "bm_registry.py" several times as HISTORICAL, purely explanatory
        # prose (why the store replaced it), which is legitimate and not this
        # test's concern.
        #
        # bm_telemetry.py's `attribute` command USED TO still load
        # bm_registry.py by path at runtime, a real, currently-broken
        # cross-fence dependency once the file was gone (it accumulated
        # spend onto a dict-shaped registry.json record that bm_store.py's
        # sqlite schema has no equivalent of at all). Fix-round 2026-07-26
        # deleted the command rather than port it: nothing in the repo
        # called it except historical design docs, and no test exercised its
        # success path, so a silent no-op was strictly worse than removing
        # it. Asserted here now that it is this test's concern again.
        self.assertFalse(os.path.exists(os.path.join(HERE, "bm_registry.py")),
                         "bm_registry.py must be deleted, not merely unreferenced")
        src = io.open(os.path.join(HERE, "bm_threads.py"), encoding="utf-8").read()
        self.assertNotIn("bm_registry", src,
                         "bm_threads.py must not reference the deleted bm_registry module")
        # bm_telemetry.py, unlike bm_threads.py, is allowed the same kind of
        # HISTORICAL prose mention bm_store.py's docstring carries (explaining
        # why `attribute` was deleted rather than ported); what must actually
        # be gone is the runtime load, so this checks for the exact spec name
        # the deleted code used rather than banning the bare word.
        tel_src = io.open(os.path.join(HERE, "bm_telemetry.py"), encoding="utf-8").read()
        self.assertNotIn("bm_registry_for_telemetry", tel_src,
                         "bm_telemetry.py must not load bm_registry.py by path at runtime "
                         "(the `attribute` command was deleted, not ported, fix-round 2026-07-26)")
        self.assertNotIn("def cmd_attribute", tel_src,
                         "cmd_attribute was deleted (fix-round 2026-07-26); it must not come back "
                         "without a store-backed spend concept to port it onto")

    def test_nothing_appends_to_the_project_state_md_any_more(self):
        # THE ARCHITECTURAL GUARD, carried from V1 and TIGHTENED by LOOP P12.
        # It used to require exactly ONE appender (_deliver_handover_once);
        # handovers are store rows now and STATE.md is a generated view, so
        # the correct number of appenders is ZERO and
        # bm_store.write_state_view is the only writer of that file anywhere.
        # A future writer that appends its own text fails here rather than
        # shipping and waiting for the erase-versus-duplicate race to be
        # rediscovered.
        threads_src = io.open(os.path.join(HERE, "bm_threads.py"), encoding="utf-8").read()
        appenders = [ln.strip() for ln in threads_src.splitlines()
                     if 'STATE_FILENAME' in ln and '"a"' in ln]
        self.assertEqual(appenders, [],
                         "nothing may append to the project STATE.md: %s" % appenders)
        self.assertNotIn('io.open(state_path, "a"', threads_src)
        writers = []
        for fn in sorted(os.listdir(HERE)):
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            src = io.open(os.path.join(HERE, fn), encoding="utf-8").read()
            for ln in src.splitlines():
                stripped = ln.strip()
                if stripped.startswith("#"):
                    continue
                if '"STATE.md"' in stripped and (
                        "safe_project_path" in stripped or "_atomic_write" in stripped):
                    writers.append((fn, stripped))
        self.assertTrue(all(fn == "bm_store.py" for fn, _ in writers),
                        "only bm_store.py may write the project STATE.md: %s" % writers)

    def test_root_resolution_is_independent_of_the_calling_subdirectory(self):
        # Closes the "working directory as identity" defect for thread
        # mode's OWN files too, not only for ownership: a command run from a
        # subdirectory must resolve the SAME project root as one run from
        # the top.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            sub = os.path.join(d, "some", "nested", "subdir")
            os.makedirs(sub)
            top = _run_threads(["dashboard"], d)
            nested = _run_threads(["dashboard"], sub)
            top_line = [l for l in top.stdout.splitlines() if l.startswith("BROTHERMODE THREADS")][0]
            nested_line = [l for l in nested.stdout.splitlines()
                           if l.startswith("BROTHERMODE THREADS")][0]
            self.assertEqual(top_line, nested_line,
                             "the resolved root must not depend on the calling subdirectory")
            self.assertIn(os.path.realpath(d), nested_line)


class TestGate4ThreadCommandsRefreshTheRootView(unittest.TestCase):
    """GATE 4 (release-blockers spec, 2026-07-26): the thread CLI never
    rendered the root STATE.md, so `bm_store.py verify` (run at every
    SessionStart) reported "1 problem(s) found" on every ordinary run
    after using thread mode, even though nothing was actually wrong."""

    def test_verify_is_healthy_after_start_with_no_off_or_dashboard(self):
        # `start` alone, never followed by `off`, `dashboard`, or any
        # bm_store.py command: the exact sequence that used to leave
        # STATE.md not reflecting the new active record.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            r = _run_threads(["start", "payments", "build payments",
                              "--files", "api/pay.py", "--session", "s1"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            problems = bs.verify(d)
            self.assertEqual(problems, [],
                             "verify must be healthy immediately after `start`, without "
                             "requiring a separate `dashboard` or `off` to refresh the view: %s"
                             % problems)

    def test_verify_is_healthy_after_checkpoint_and_decide_too(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments",
                          "--files", "api/pay.py", "--session", "s1"], d)
            _run_threads(["checkpoint", "payments", "--next", "wire it"], d)
            _run_threads(["decide", "payments", "--topic", "t", "--text", "x"], d)
            problems = bs.verify(d)
            self.assertEqual(problems, [], problems)

    def test_calibrated_gate4_reinjecting_a_skipped_refresh_reproduces_the_problem(self):
        # CALIBRATION: reinject a no-op onto the real PRODUCT symbol
        # threads_mod._refresh_root_view, called IN-PROCESS (a subprocess
        # cannot see this monkeypatch), and confirm the SAME `start`
        # sequence now reproduces the reported "1 problem(s) found".
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            original = threads_mod._refresh_root_view
            threads_mod._refresh_root_view = lambda root: None
            try:
                code, printed = _call_thread_cmd_in_process(
                    d, threads_mod.cmd_start,
                    ["payments", "build", "payments", "--files", "api/pay.py", "--session", "s1"])
            finally:
                threads_mod._refresh_root_view = original
            self.assertEqual(code, 0, printed)
            problems = bs.verify(d)
            self.assertEqual(
                len(problems), 1,
                "REINJECTION CHECK: with the view refresh disabled, `start` must "
                "leave verify() reporting exactly the GATE 4 problem shape; if this "
                "assertion fails, the tests above are not calibrated to the defect "
                "they claim to catch: %s" % problems)
            self.assertTrue(any("STATE.md does not exist" in p
                                or "does not appear in the generated" in p for p in problems),
                            problems)


class TestGate5UnknownFlagsRefused(unittest.TestCase):
    """GATE 5 (release-blockers spec, 2026-07-26): neither CLI validated
    flag names. `start X --file f` (singular) created a thread whose
    objective was the flag text and with NO FENCE, exit 0. `checkpoint X
    --note "..."` reported success and stored nothing. A silently unfenced
    thread breaks the one guarantee this project exists to provide."""

    def test_start_refuses_a_misspelled_files_flag(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            r = _run_threads(["start", "X", "objective", "text", "--file", "f"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("unrecognized flag", r.stdout)
            self.assertIsNone(_record(d, "X"),
                             "a refused start must create NO record at all, fenced or not")

    def test_checkpoint_refuses_a_misspelled_note_flag(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "payments", "build payments",
                          "--files", "api/pay.py", "--session", "s1"], d)
            before = _record(d, "payments")
            r = _run_threads(["checkpoint", "payments", "--note", "some progress note"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("unrecognized flag", r.stdout)
            after = _record(d, "payments")
            self.assertEqual(before["version"], after["version"],
                             "a refused checkpoint must record nothing, not a "
                             "version bump with an empty digest")

    def test_calibrated_gate5_reinjecting_permissive_start_lets_a_typo_through_unfenced(self):
        # CALIBRATION: reinject a no-op onto the real PRODUCT symbol
        # threads_mod._unrecognized_start_flags (start's OWN flag check;
        # it cannot use _reject_unknown_flags, which checks an already-
        # parsed kv dict, since an unrecognized flag here corrupts parsing
        # itself), called IN-PROCESS, and confirm the SAME `--file` typo
        # now reproduces the exact reported defect: a thread created with
        # the flag text as its objective and NO FENCE, at exit 0.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            original = threads_mod._unrecognized_start_flags
            threads_mod._unrecognized_start_flags = lambda *a, **k: []
            try:
                code, printed = _call_thread_cmd_in_process(
                    d, threads_mod.cmd_start, ["X", "objective", "text", "--file", "f"])
            finally:
                threads_mod._unrecognized_start_flags = original
            self.assertEqual(
                code, 0, "REINJECTION CHECK: with flag validation disabled, `start "
                "X --file f` must succeed at exit 0 (the reported defect), not "
                "refuse for some other reason: %s" % printed)
            rec = _record(d, "X", states=("active",))
            self.assertIsNotNone(rec, "REINJECTION CHECK: the old behavior still "
                                 "creates a record, just an unfenced one")
            self.assertIn("--file f", rec["objective"],
                          "REINJECTION CHECK: the flag text must have leaked into "
                          "the objective, the exact reported shape")
            self.assertEqual(_claims(d, rec["lifecycle_uuid"]), [],
                             "REINJECTION CHECK: the record must have NO FENCE, "
                             "the exact reported defect")


class TestCliSequencesNeverRaiseOrLeaveDoubleActiveThreads(unittest.TestCase):
    """A narrower answer to the same testing defect V1's giant random-sequence
    generator targeted, rescoped for Phase 3.

    V1's version asserted content-level invariants (I1-I9 in the old,
    now-retired INVARIANTS.md) against the exact shape of registry.json and
    thread-mode.json, several of which describe files or a policy ("every
    path exits 0") that no longer exist: ownership operations now FAIL
    CLOSED on purpose (a live design decision, not an oversight this test
    would be catching). Losslessness, single-writer, crash-atomicity, and
    exactly-once delivery are the STORE's job now and are exercised
    adversarially by test_bm_store.py's own 182 tests, including its own
    concurrency coverage.

    What is still this module's own job, and what nothing else isolates on
    its own, is that the thin CLI WRAPPER never turns a store refusal into
    an unhandled traceback, and that a completed `off` never leaves the
    system internally inconsistent (mode says off, a persistent record
    still says active). This is intentionally SMALLER in scope than the
    320-line generator it replaces; see the implementer's report for why a
    full port was not attempted."""

    OPS = ("start", "checkpoint", "park", "resume", "complete", "adopt", "send", "off")

    def test_short_random_sequences_stay_honest(self):
        import random
        for seed in range(6):
            self._run_sequence(seed)

    def _run_sequence(self, seed, steps=14):
        import random
        rng = random.Random(seed)
        names = ["alpha", "beta"]
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            for step in range(steps):
                name = rng.choice(names)
                rec = _record(d, name)
                op = rng.choice(self.OPS)
                args = {
                    "start": ["start", name, "objective %d" % step, "--files",
                              "api/%s_%d.py" % (name, rng.randint(0, 1)), "--session", "s1"],
                    "checkpoint": ["checkpoint", name, "--next", "next: T%d" % step],
                    "park": ["park", name, "--session", "s1"],
                    "resume": ["resume", name, "--session", "s1"],
                    "complete": ["complete", name, "--session", "s1", "--evidence", "ok"],
                    "adopt": ["adopt", name],
                    "send": ["send", name, "a directive"],
                    "off": ["off"],
                }[op]
                r = _run_threads(args, d)
                where = "seed=%d step=%d op=%s rec=%r" % (seed, step, op, rec)
                self.assertNotIn("Traceback", r.stderr, "unhandled exception (%s): %s" % (where, r.stderr))
                self.assertIn(r.returncode, (0, 2),
                              "exit code must be 0 (ok) or 2 (a clean, named refusal), "
                              "never anything else (%s): %d" % (where, r.returncode))
                if op == "off" and r.returncode == 0:
                    mode = json.load(io.open(os.path.join(d, "threads", "thread-mode.json")))
                    if mode.get("mode") == "off":
                        data = _dump(d)
                        still_active = [x["name"] for x in data["records"]
                                       if x["state"] == "active" and x["lifetime"] == "persistent"]
                        self.assertEqual(still_active, [],
                                         "off reported success (%s) but left an active "
                                         "persistent record: %s" % (where, still_active))


class TestGate3OutputFunnelCoversTheMcpServerToo(unittest.TestCase):
    """GATE 3 (final-blockers spec 2026-07-26): the round-7 output-funnel
    structural scan (test_bm_store.py's own
    test_structural_round7_no_print_or_raw_stream_write_anywhere) only
    ever looked at bm_store.py itself, so mcp/bm_mcp_server.py, added the
    same day, sat entirely outside the guard: it called bm_store.verify()
    and printed the raw problem strings straight into a tool response with
    no redaction at all, while bm_store.py's own CLI showed [REDACTED] for
    the identical value. "A guard that covers one file keeps being escaped
    by the next file" is the literal lesson, so this widens the SAME scan
    (imported from test_bm_store.py rather than re-implemented, so there
    is exactly one regex to keep correct across both files) onto
    mcp/bm_mcp_server.py, and separately proves the leak itself is closed
    end to end against the real tool function.

    NOT widened to bm_threads.py, bm_autosave.py, bm_telemetry.py, or
    bm_score.py: none of them was ever brought under this funnel
    discipline in the first place (bm_telemetry.py alone has dozens of
    ordinary print() call sites doing plain CLI output with no equivalent
    _out()/_warn() primitive to route through), and all four are OUTSIDE
    this fix's fence (docs/superpowers/specs/2026-07-26-final-blockers.md
    names them out of bounds for this change). Scanning them here would
    fail on pre-existing code this fence forbids touching, not on
    anything this change introduced; narrowed here rather than faked, and
    stated plainly rather than left implicit."""

    MCP_SERVER_PATH = os.path.join(HERE, "..", "mcp", "bm_mcp_server.py")

    @staticmethod
    def _load_scanner():
        # Reused, not re-implemented: the exact regex test_bm_store.py's
        # own round-7 scan already uses, loaded from that file (the same
        # by-path loading convention this file already uses for
        # bm_telemetry/bm_store/bm_threads above) so there is exactly one
        # definition of "forbidden" to keep correct across both files.
        spec = importlib.util.spec_from_file_location(
            "test_bm_store_for_funnel_scan", os.path.join(HERE, "test_bm_store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._scan_for_forbidden_output_calls

    @staticmethod
    def _load_mcp_server():
        spec = importlib.util.spec_from_file_location(
            "bm_mcp_server_for_test", os.path.join(HERE, "..", "mcp", "bm_mcp_server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_mcp_server_has_no_bare_print_or_raw_stream_write(self):
        scan = self._load_scanner()
        with io.open(self.MCP_SERVER_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        hits = scan(lines)
        self.assertEqual(hits, [],
                          "bare print()/stream.write() call site(s) found in "
                          "mcp/bm_mcp_server.py outside its own _raw_write "
                          "primitive, at line(s): %s" % hits)

    def test_calibrated_scan_catches_a_reintroduced_bare_write(self):
        # Calibration, the same shape as test_bm_store.py's own companion
        # test: proves the imported scanner genuinely flags the exact
        # defect class this guard exists to catch, on a synthetic
        # snippet, rather than trusting an untested regex import.
        scan = self._load_scanner()
        bad_source = ["def tool_bm_status(arguments):\n",
                      "    sys.stdout.write('leaked')\n"]
        self.assertEqual(scan(bad_source), [2])
        good_source = ["def tool_bm_status(arguments):\n",
                       "    _raw_write(sys.stdout, 'safe')\n"]
        self.assertEqual(scan(good_source), [])

    def test_calibrated_mcp_server_redacts_verify_problems_before_they_leave_the_tool(self):
        # THE reproduction this gate exists to close: a record NAME shaped
        # like a real secret used to reach bm_status completely unredacted,
        # because bm_store.verify() returned RAW rows and the server printed
        # them straight through.
        #
        # LOOP 5 (2026-07-31) CLOSED THE SOURCE: verify() now scrubs its own
        # problem strings (redact_text plus mask_absolute_paths), so the
        # natural reproduction this test used to stage (a secret-shaped
        # record name surfacing raw in problems) CANNOT happen any more, and
        # the old precondition assert started failing for the right reason.
        #
        # The funnel is still worth its own calibration: it is the defense
        # IN DEPTH for any future verify() code path that forgets to scrub.
        # So the leak is INJECTED now, by stubbing verify on the one shared
        # bm_store module the server calls through (bm_store.verify(snap),
        # mcp/bm_mcp_server.py), and the funnel must still redact it.
        mcp_mod = self._load_mcp_server()
        secret = "AKIAIOSFODNN7EXAMPLE"
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            store = bs.Store(root)
            try:
                store.claim("innocent-name", "persistent", "objective",
                            ["some/file.py"], session_id="s1")
            finally:
                store.close()
            # First, pin the Loop 5 source fix itself: the natural staging
            # that used to leak must now come back scrubbed.
            with io.open(os.path.join(root, "STATE.md"), "w", encoding="utf-8") as f:
                f.write(bs._STATE_BEGIN + "\n(nothing rendered yet)\n" + bs._STATE_END + "\n")
            natural = bs.verify(root)
            self.assertTrue(natural, "the truncated STATE.md must still "
                                     "surface at least one problem")
            # Then calibrate the funnel against an injected raw leak. The
            # stub must land on the server's OWN bm_store: _load_bm_store()
            # gives it a private module ("bm_store_for_mcp"), so patching
            # this file's `bs` would never reach it, and the first draft of
            # this rewrite proved that by watching the real verify() run.
            original_verify = mcp_mod.bm_store.verify
            def leaky_verify(r, *a, **kw):
                return ["active record %s is missing from STATE.md" % secret]
            mcp_mod.bm_store.verify = leaky_verify
            try:
                text = mcp_mod.tool_bm_status({"project_root": root})
            finally:
                mcp_mod.bm_store.verify = original_verify
            self.assertNotIn(secret, text,
                             "GATE 3 regression: the injected secret reached "
                             "the tool's returned text")
            self.assertIn("[REDACTED]", text)


class TestFinding3McpServerRefusesSourcesOutsideTheRequestedRoot(unittest.TestCase):
    """FINDING 3 (HIGH, cross-project disclosure, EXECUTED AND CONFIRMED
    2026-07-27, not theorised): mcp/bm_mcp_server.py probed for a project's
    .brothermode marker with os.path.isdir, which FOLLOWS a directory
    symlink, and then copied that directory with shutil.copytree at its
    default symlinks=False, which DEREFERENCES. With project-b/.brothermode
    symlinked at project-a/.brothermode, a tools/call naming ONLY project B
    came back with project A's confidential objective and A's fenced path,
    isError: false, over a real stdio JSON-RPC session.

    bm_store.ReadOnlyStore's own containment checks (bm_store.py, the
    _refuse_if_symlink_escape / _refuse_if_hardlinked block in its
    __init__) could never catch this: the server hands ReadOnlyStore a
    SNAPSHOT root, and by then the snapshot's .brothermode is a real,
    already-dereferenced copy with nothing wrong with it. The escape
    happened during the copy, so the check has to run against the root the
    CALLER named, which is what the server now does before it probes or
    copies anything.

    Driven end to end through the real server process over stdio, not by
    calling the tool function in-process: the defect lived in the handshake
    between root resolution and the snapshot copy, and an in-process call
    would prove less than the thing an MCP client actually does. Both
    throwaway projects live under a temporary directory, never a real
    project and never the founder's home."""

    MCP_SERVER_PATH = os.path.join(HERE, "..", "mcp", "bm_mcp_server.py")
    SECRET_OBJECTIVE = "PROJECT-A-CONFIDENTIAL-OBJECTIVE-DO-NOT-DISCLOSE"
    SECRET_RECORD = "secretworkfinding3"
    SECRET_CLAIM = "src/a-private-finding3.py"

    def _make_project_a(self, base):
        """A throwaway project holding one active record whose name,
        objective, and claimed path are all distinctive enough that finding
        any of them in another project's answer is unambiguous."""
        a = os.path.join(base, "project-a")
        os.makedirs(a)
        store = bs.Store(a)
        try:
            store.claim(self.SECRET_RECORD, "ephemeral", self.SECRET_OBJECTIVE,
                        [self.SECRET_CLAIM], session_id="s1")
        finally:
            store.close()
        return a

    def _drive_server(self, root, tool):
        """One real stdio JSON-RPC session against the real server process:
        initialize, notifications/initialized, then one tools/call. Returns
        (isError, text, combined_stdout_and_stderr) so a test can assert
        both on what the client was told and on what the process emitted
        anywhere at all. BROTHERMODE_ROOT is scrubbed from the child's
        environment, matching _run_threads above, so ambient developer state
        can never decide the answer."""
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test_bm", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": tool, "arguments": {"project_root": root}}},
        ]
        env = dict(os.environ)
        env.pop("BROTHERMODE_ROOT", None)
        p = subprocess.run(
            [sys.executable, self.MCP_SERVER_PATH],
            input="".join(json.dumps(m) + "\n" for m in msgs),
            capture_output=True, text=True, env=env)
        text = None
        is_error = None
        for line in p.stdout.splitlines():
            obj = json.loads(line)
            if obj.get("id") == 3:
                result = obj.get("result") or {}
                is_error = result.get("isError")
                text = "".join(c.get("text", "") for c in result.get("content", []))
        self.assertIsNotNone(text, "server sent no tools/call result; stderr: %s" % p.stderr)
        return is_error, text, p.stdout + p.stderr

    def _assert_no_project_a_data(self, everything, where):
        for secret in (self.SECRET_OBJECTIVE, self.SECRET_RECORD, self.SECRET_CLAIM):
            self.assertNotIn(
                secret, everything,
                "FINDING 3 regression: project A's %r reached the client (or "
                "this process's output) from a call that named only project "
                "B, via %s" % (secret, where))

    def test_symlinked_brothermode_directory_cannot_disclose_another_project(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            a = self._make_project_a(base)
            b = os.path.join(base, "project-b")
            os.makedirs(b)
            # Project B has no store of its own: its .brothermode IS project
            # A's, reached through a directory symlink.
            os.symlink(bs.store_dir(a), bs.store_dir(b))
            for tool in ("bm_active_work", "bm_fences", "bm_decisions", "bm_status"):
                is_error, text, everything = self._drive_server(b, tool)
                self.assertTrue(
                    is_error,
                    "%s answered isError: false for a project whose "
                    ".brothermode is a symlink into another project; it must "
                    "refuse: %s" % (tool, text))
                self.assertIn("refusing to read", text)
                self._assert_no_project_a_data(everything, tool)

    def test_symlinked_store_file_inside_a_real_brothermode_is_refused(self):
        # The narrower door: project B's .brothermode is a genuine directory
        # of its own, so the marker probe is satisfied, and only the store
        # FILE inside it links out. bm_store.py closed this exact class for
        # itself (GATE B, its _refuse_if_symlink_escape covers the store file
        # and its -wal/-shm sidecars); this proves the MCP server now applies
        # that same check to the root a caller named, where it can still fire.
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            a = self._make_project_a(base)
            b = os.path.join(base, "project-b")
            os.makedirs(bs.store_dir(b))
            os.symlink(bs.store_path(a), bs.store_path(b))
            is_error, text, everything = self._drive_server(b, "bm_active_work")
            self.assertTrue(
                is_error,
                "a symlinked .brothermode/store.sqlite3 was not refused: %s" % text)
            self.assertIn("refusing to read", text)
            self._assert_no_project_a_data(everything, "symlinked store file")

    def test_refusal_never_names_what_the_link_resolves_to(self):
        # A refusal that prints the resolved target hands back a smaller
        # piece of exactly the cross-project information it is denying: the
        # other project's absolute path.
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            a = self._make_project_a(base)
            b = os.path.join(base, "project-b")
            os.makedirs(b)
            os.symlink(bs.store_dir(a), bs.store_dir(b))
            _is_error, text, _everything = self._drive_server(b, "bm_active_work")
            self.assertNotIn(bs.store_dir(a), text)
            self.assertIn(bs.store_dir(b), text)

    def test_an_ordinary_project_still_answers_normally(self):
        # The control this fence needs: a refusal that fires on everything
        # would also "pass" the tests above while breaking the server. A
        # plain project, no links anywhere, must still answer with its own
        # record and isError: false.
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            store = bs.Store(root)
            try:
                store.claim("ordinary", "ephemeral", "an ordinary objective",
                            ["src/ordinary.py"], session_id="s1")
            finally:
                store.close()
            is_error, text, _everything = self._drive_server(root, "bm_active_work")
            self.assertFalse(is_error, "an ordinary project was refused: %s" % text)
            self.assertIn("ordinary", text)
            # LOOP 11: the objective is founder prose, so the MCP response
            # carries the withholding marker instead of the text. The record
            # is still identifiable (name and uuid prefix), which is what
            # "answers normally" has to mean once the export policy applies.
            self.assertNotIn("an ordinary objective", text)
            self.assertIn("objective=[WITHHELD:", text)


class TestFinding3bInternalErrorsGoThroughTheOutputFunnel(unittest.TestCase):
    """FINDING 3b (LOW, output funnel bypass, 2026-07-27): the catch-all in
    mcp/bm_mcp_server.py's handle_tools_call returned "internal error: %r"
    % (e,) straight to the client. Every other response in that file passes
    through _protect (redact, then sanitize); this one did not, so an
    exception repr, which is not server-authored text and can carry
    absolute paths and store rows the tool was mid-way through rendering,
    bypassed the funnel entirely. Calibrated the same way the GATE 3 test
    above is: force the real handler to raise an exception carrying a
    secret-shaped value, then assert the client's response does not carry
    it."""

    def test_an_unexpected_exception_never_returns_its_own_repr(self):
        mcp_mod = TestGate3OutputFunnelCoversTheMcpServerToo._load_mcp_server()
        secret = "AKIAIOSFODNN7EXAMPLE"

        def exploding_tool(_arguments):
            raise RuntimeError("leaked %s from /private/some/real/path" % secret)

        original = mcp_mod.TOOL_HANDLERS["bm_status"]
        mcp_mod.TOOL_HANDLERS["bm_status"] = exploding_tool
        try:
            response = mcp_mod.handle_tools_call({
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "bm_status", "arguments": {"project_root": "/nowhere"}},
            })
        finally:
            mcp_mod.TOOL_HANDLERS["bm_status"] = original
        text = "".join(c.get("text", "") for c in response["result"]["content"])
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(secret, text,
                         "FINDING 3b regression: the exception's own text reached the client")
        self.assertNotIn("/private/some/real/path", text)
        self.assertIn("RuntimeError", text)


class TestFinding4InstallVerifierSeesEveryEntryType(unittest.TestCase):
    """FINDING 4 and 4B (2026-07-27, REPRODUCED BEFORE THE FIX): the install
    verifier could not see a symlink at all, and the manifest never recorded
    what KIND of entry it was attesting.

    scripts/verify-install.sh enumerated the installed tree with
    `find "$TARGET" -type f`. POSIX find without -L does not follow symlinks,
    so a symlink is -type l and never matches -type f: an unmanifested
    symlink was invisible to the extra-entry scan, and invisible to the
    manifest scan too, because the manifest had never named it. The verifier
    printed PASSED and exited 0 with a backdoor sitting in the tree. That is
    a live code-execution path, not a tidiness problem: planting
    tools/json.py as a symlink shadows the standard library for every session
    hook that runs a tool out of tools/, because Python puts a script's own
    directory at the front of sys.path.

    scripts/checksums.sh had the mirror-image hole: `[ ! -f "$f" ]` follows
    symlinks, so a tracked symlink pointing at a regular file passed the
    guard and got its TARGET's bytes hashed into a manifest line that looked
    exactly like an ordinary file.

    Every assertion below was run against the pre-fix scripts and observed
    failing (exit 0 where it now demands nonzero) before the fix landed.
    Each test builds its own throwaway install under a temporary directory
    and never reads or writes the real repository or the real home
    directory.
    """

    SCRIPTS_DIR = os.path.join(os.path.dirname(HERE), "scripts")

    def _make_install(self, root):
        """Build a throwaway install: the two real scripts, a couple of
        ordinary files, and a manifest generated by the real checksums.sh.
        The tree is a git repo so that checksums.sh exercises its primary
        `git ls-files -s` path, the one a real release goes through."""
        os.makedirs(os.path.join(root, "scripts"))
        os.makedirs(os.path.join(root, "tools"))
        for name in ("checksums.sh", "verify-install.sh"):
            shutil.copy2(os.path.join(self.SCRIPTS_DIR, name),
                         os.path.join(root, "scripts", name))
        with io.open(os.path.join(root, "tools", "bm_telemetry.py"), "w",
                     encoding="utf-8") as f:
            f.write("# throwaway stand-in, not the real tool\n")
        with io.open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
            f.write("throwaway\n")
        for args in (["init", "-q"], ["add", "-A"]):
            r = subprocess.run(["git", "-C", root] + args,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = self._checksums(root)
        self.assertEqual(r.returncode, 0,
                         "checksums.sh failed on a clean throwaway tree: %s" % r.stderr)

    def _checksums(self, root):
        return subprocess.run(
            ["sh", os.path.join(root, "scripts", "checksums.sh"), "CHECKSUMS.sha256"],
            cwd=root, capture_output=True, text=True)

    def _verify(self, root):
        """Run the verifier as a script and read the SCRIPT's own exit code.
        Never through a pipeline: `sh script | head` reports head's status,
        not the script's, and that has produced a false pass here before."""
        return subprocess.run(
            ["sh", os.path.join(root, "scripts", "verify-install.sh"),
             os.path.join(root, "CHECKSUMS.sha256"), root],
            capture_output=True, text=True)

    def test_clean_install_still_passes(self):
        # Calibration for the three tests below: without this, a verifier
        # that failed on absolutely everything would satisfy them all.
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            r = self._verify(root)
            self.assertEqual(r.returncode, 0,
                             "clean throwaway install should verify:\n%s\n%s"
                             % (r.stdout, r.stderr))
            self.assertIn("PASSED", r.stdout)

    def test_venv_embed_directory_is_excluded_from_the_extra_scan(self):
        """The vault lane's local venv (tools/bm_embed_bge.py) lands at the
        repository root and is full of symlinks by construction. Proof 5
        and 6 of the .venv-embed installer fix: checksums.sh must still
        pass with a real venv present (it is untracked, so `git ls-files`
        never sees it), and verify-install.sh must still report PASSED
        with the installed file set matching the manifest exactly, rather
        than raising its loud "EXTRA (symlink)" alarm (the same alarm
        FINDING 4 below exists to raise for a genuinely planted backdoor)
        against every file inside a venv nobody planted maliciously."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            venv_dir = os.path.join(root, ".venv-embed")
            v = subprocess.run([sys.executable, "-m", "venv", venv_dir],
                               capture_output=True, text=True, timeout=120)
            if v.returncode != 0:
                self.skipTest("python3 -m venv could not build the "
                              "fixture: %s" % (v.stdout + v.stderr))
            r = self._checksums(root)
            self.assertEqual(r.returncode, 0,
                             "checksums.sh must still pass with a real "
                             ".venv-embed present:\n%s\n%s"
                             % (r.stdout, r.stderr))
            r = self._verify(root)
            self.assertEqual(r.returncode, 0,
                             "verify-install.sh must still pass with a "
                             "real .venv-embed present:\n%s\n%s"
                             % (r.stdout, r.stderr))
            self.assertIn("PASSED", r.stdout)
            self.assertNotIn(".venv-embed", r.stdout,
                             "the installed-file verification must not "
                             "surface .venv-embed as an EXTRA entry at "
                             "all: %s" % r.stdout)

    def test_unmanifested_symlink_file_is_rejected_and_named(self):
        # TEST A. The backdoor shape: tools/json.py pointing at an
        # attacker-controlled file, shadowing the standard library for
        # anything run out of tools/. Pre-fix this exited 0 and said PASSED.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = os.path.realpath(d)
            self._make_install(root)
            evil = os.path.join(os.path.realpath(outside), "evil.py")
            with io.open(evil, "w", encoding="utf-8") as f:
                f.write("# attacker controlled\n")
            os.symlink(evil, os.path.join(root, "tools", "json.py"))
            r = self._verify(root)
            self.assertNotEqual(r.returncode, 0,
                                "FINDING 4 regression: an unmanifested symlink "
                                "passed verification:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("tools/json.py", r.stdout,
                          "the verifier failed but never named the planted entry")
            self.assertIn("FAILED", r.stderr)

    def test_unmanifested_symlinked_directory_is_rejected(self):
        # TEST B. A symlink to a DIRECTORY is still -type l, so -type f
        # missed it exactly the same way, and find is deliberately not asked
        # to descend through it (no -L) so nothing under a planted link is
        # traversed or trusted.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = os.path.realpath(d)
            self._make_install(root)
            evil_dir = os.path.join(os.path.realpath(outside), "payloads")
            os.makedirs(evil_dir)
            with io.open(os.path.join(evil_dir, "payload.py"), "w",
                         encoding="utf-8") as f:
                f.write("# attacker controlled\n")
            os.symlink(evil_dir, os.path.join(root, "tools", "vendor"))
            r = self._verify(root)
            self.assertNotEqual(r.returncode, 0,
                                "FINDING 4 regression: an unmanifested symlinked "
                                "directory passed verification:\n%s\n%s"
                                % (r.stdout, r.stderr))
            self.assertIn("tools/vendor", r.stdout)

    def test_manifested_file_swapped_for_a_symlink_is_rejected(self):
        # The type half of finding 4: same bytes, different entry type. A
        # content hash cannot catch this on its own, because the hash of the
        # link's target matches perfectly. Only an lstat does.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = os.path.realpath(d)
            self._make_install(root)
            manifested = os.path.join(root, "tools", "bm_telemetry.py")
            decoy = os.path.join(os.path.realpath(outside), "same_bytes.py")
            shutil.copy2(manifested, decoy)
            os.remove(manifested)
            os.symlink(decoy, manifested)
            r = self._verify(root)
            self.assertNotEqual(r.returncode, 0,
                                "FINDING 4 regression: a manifested regular file "
                                "swapped for a symlink with identical target bytes "
                                "passed verification:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("TYPESWAP", r.stdout)
            self.assertIn("tools/bm_telemetry.py", r.stdout)

    def test_checksums_refuses_to_manifest_a_symlink(self):
        # FINDING 4B: pre-fix, this wrote a manifest whose line for the
        # symlink carried the hash of the LINK TARGET's bytes, with nothing
        # recording that the entry was a link or where it pointed. The
        # manifest's type claim (every entry is a regular file) is
        # established here, by refusing, which is what lets the verifier
        # treat a symlink at a manifested path as an attack.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = os.path.realpath(d)
            self._make_install(root)
            target = os.path.join(os.path.realpath(outside), "target.txt")
            with io.open(target, "w", encoding="utf-8") as f:
                f.write("bytes that are not shipped by this repo\n")
            os.symlink(target, os.path.join(root, "tools", "sneaky.py"))
            r = subprocess.run(["git", "-C", root, "add", "-A"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            modes = subprocess.run(["git", "-C", root, "ls-files", "-s",
                                    "tools/sneaky.py"], capture_output=True, text=True)
            self.assertTrue(modes.stdout.startswith("120000"),
                            "test setup failed: git did not track the entry as a "
                            "symlink, so this does not reproduce finding 4B (%r)"
                            % modes.stdout)
            r = self._checksums(root)
            self.assertNotEqual(r.returncode, 0,
                                "FINDING 4B regression: checksums.sh manifested a "
                                "symlink:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("tools/sneaky.py", r.stderr)

    def _strip_git(self, root):
        """A copy of `root` with no .git, which is what a released install
        actually is: a plain directory. Both scripts take a different branch
        there (checksums.sh walks with find instead of asking git; the
        verifier keeps its full EXTRA alarm instead of consulting
        .gitignore), and that is the branch E80 was found on."""
        plain = os.path.join(os.path.dirname(root), "plain-" + os.path.basename(root))
        shutil.copytree(root, plain,
                        ignore=shutil.ignore_patterns(".git", "CHECKSUMS.sha256"))
        return plain

    def _add_templates(self, root):
        os.makedirs(os.path.join(root, "project-template"), exist_ok=True)
        for name in ("CANVAS.md", "DELIVERY-PACKET.md"):
            with io.open(os.path.join(root, "project-template", name),
                         "w", encoding="utf-8") as f:
                f.write("shipped template, not project state\n")

    def test_a_shipped_template_named_like_project_state_is_manifested(self):
        """E80 (2026-09-04, external release integrity trial on the public
        v1.0.1 clone). checksums.sh excluded CANVAS.md and
        DELIVERY-PACKET.md by BARE NAME, which matches at ANY depth, and the
        product ships project-template/CANVAS.md and
        project-template/DELIVERY-PACKET.md as tracked template files. On a
        tree with no .git (a released install, an export) the find branch
        therefore dropped two shipped files out of the manifest entirely:
        nothing attested them, so nothing could ever detect them being
        changed.

        Driven against the pre-fix script and observed failing: the manifest
        came back without either path."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            self._add_templates(root)
            plain = self._strip_git(root)
            r = self._checksums(plain)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(os.path.join(plain, "CHECKSUMS.sha256"),
                         encoding="utf-8") as fh:
                manifest = fh.read()
            for rel in ("project-template/CANVAS.md",
                        "project-template/DELIVERY-PACKET.md"):
                self.assertIn(rel, manifest,
                              "checksums.sh dropped the shipped template %r "
                              "from the manifest (E80)" % rel)
            r = self._verify(plain)
            self.assertEqual(r.returncode, 0,
                             "a shipped template named like project state "
                             "must verify:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("PASSED", r.stdout)

    def test_an_unmanifested_file_named_like_project_state_is_still_an_extra(self):
        """The verifier's half of E80, which is the security-relevant one:
        its EXTRA scan skipped ANY entry named CANVAS.md, so a planted file
        at tools/CANVAS.md sat in an installed copy and the verifier printed
        PASSED. The exclusion is now anchored at the install root, where the
        founder's own project state actually lands, so a plant one directory
        down is named and fails the run.

        Driven against the pre-fix script and observed failing: exit 0 and
        PASSED with the plant in place."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            plain = self._strip_git(root)
            r = self._checksums(plain)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(os.path.join(plain, "tools", "CANVAS.md"), "w",
                         encoding="utf-8") as f:
                f.write("planted, never manifested\n")
            r = self._verify(plain)
            self.assertNotEqual(r.returncode, 0,
                                "an unmanifested tools/CANVAS.md must not "
                                "pass:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("tools/CANVAS.md", r.stdout)

    def test_project_state_at_the_install_root_is_still_excluded(self):
        """The other half of E80, so the fix cannot be 'stop excluding
        anything'. A CANVAS.md the founder's own project wrote at the
        install ROOT is state, never payload: it stays out of the manifest
        and out of the verifier's listing, so no EXTRA alarm fires for it
        on a machine where anyone has ever started a project."""
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            plain = self._strip_git(root)
            r = self._checksums(plain)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(os.path.join(plain, "CHECKSUMS.sha256"),
                         encoding="utf-8") as fh:
                manifest = fh.read()
            for name in ("CANVAS.md", "CANVAS-p1.md", "DELIVERY-PACKET.md",
                         "DELIVERY-PACKET-p1.md", "PROJECT-VIEW.html",
                         "STATE.md"):
                with io.open(os.path.join(plain, name), "w",
                             encoding="utf-8") as f:
                    f.write("machine state written by a running project\n")
                self.assertNotIn(
                    "  %s\n" % name, manifest,
                    "root-level project state %r must not be manifested" % name)
            r = self._verify(plain)
            self.assertEqual(r.returncode, 0,
                             "project state at the install root must not "
                             "raise EXTRA:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("PASSED", r.stdout)

    # -- E97, 2026-09-04. t5's failure-injection gauntlet forced line 195's
    # `if [ "$actual" = "$expected" ]; then` to `if true` and no suite here
    # noticed, because every test above plants an EXTRA or TYPESWAP entry
    # rather than tampering the bytes of one already-manifested file. This
    # closes exactly that gap.

    def test_a_tampered_manifested_file_is_named_mismatch_then_restoring_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            self._make_install(root)
            manifested = os.path.join(root, "tools", "bm_telemetry.py")
            with io.open(manifested, encoding="utf-8") as fh:
                original_bytes = fh.read()

            r = self._verify(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASSED", r.stdout)

            with io.open(manifested, "a", encoding="utf-8") as fh:
                fh.write("X")  # one tampered byte
            r = self._verify(root)
            self.assertNotEqual(r.returncode, 0,
                                "a single tampered byte in a manifested file "
                                "must not pass:\n%s\n%s" % (r.stdout, r.stderr))
            self.assertIn("FAILED", r.stderr)
            self.assertIn("MISMATCH:  tools/bm_telemetry.py", r.stdout)

            with io.open(manifested, "w", encoding="utf-8") as fh:
                fh.write(original_bytes)
            r = self._verify(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASSED", r.stdout)

    def test_gutting_the_compare_to_always_true_misses_the_same_tamper(self):
        """Proves the test above actually watches line 195: a throwaway copy
        of verify-install.sh, kept OUTSIDE the install tree so it is never
        itself walked as an unmanifested EXTRA entry, with only that one
        comparison replaced by `if true`, reports PASSED over the identical
        single-byte tamper the real script just caught above."""
        compare_line = 'if [ "$actual" = "$expected" ]; then'
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = os.path.realpath(d)
            self._make_install(root)
            manifested = os.path.join(root, "tools", "bm_telemetry.py")
            with io.open(manifested, "a", encoding="utf-8") as fh:
                fh.write("X")

            real_script = os.path.join(root, "scripts", "verify-install.sh")
            with io.open(real_script, encoding="utf-8") as fh:
                source = fh.read()
            self.assertEqual(source.count(compare_line), 1,
                             "the compare line moved; update this fixture to match")
            gutted = os.path.join(os.path.realpath(outside), "verify-install-gutted.sh")
            with io.open(gutted, "w", encoding="utf-8") as fh:
                fh.write(source.replace(compare_line, "if true; then"))
            os.chmod(gutted, 0o755)

            r = subprocess.run(
                ["sh", gutted, os.path.join(root, "CHECKSUMS.sha256"), root],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASSED", r.stdout)
            self.assertNotIn("MISMATCH", r.stdout)


def _identity_rows(root, name):
    """Every record named `name`, read through the store's own direct table
    lookup. Deliberately NOT _record() above, which reads dump(): a name
    that trips the redactor comes back as "[REDACTED]" from dump(), so a
    test that used it could never see the very records FINDING 9 is about."""
    store = bs.Store(root, create=False)
    try:
        return store.identity_by_name(name)
    finally:
        store.close()


def _fresh_threads_module(tag):
    """A private import of bm_threads.py, so a calibration test can
    monkeypatch product symbols on it without leaking that patch into any
    other test (the shape TestPartialOffAtomicity._mod already uses)."""
    spec = importlib.util.spec_from_file_location(
        "bm_threads_%s" % tag, os.path.join(HERE, "bm_threads.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _OLD_find_record_through_dump(store, name, states=None, lifecycle_prefix=None):
    """The PRE-FIX _find_record, verbatim in behaviour: it resolved names
    through the redacted presentation view (FINDING 9) and returned the most
    recently updated match whenever the top two timestamps merely differed
    (FINDING 10). Kept here, in the test file, so the calibration tests can
    reinject the exact old behaviour without the product file carrying a
    dead code path someone could call by accident."""
    data = store.dump()
    matches = [r for r in data["records"] if r["name"] == name]
    if lifecycle_prefix:
        matches = [r for r in matches if r["lifecycle_uuid"].startswith(lifecycle_prefix)]
    if states:
        matches = [r for r in matches if r["state"] in states]
    if not matches:
        raise bs.OwnershipRefused("not-found", "no record named %r found" % (name,))
    if len(matches) > 1:
        matches.sort(key=lambda r: r["updated_at"], reverse=True)
        if matches[0]["updated_at"] != matches[1]["updated_at"]:
            return matches[0]
        raise bs.OwnershipRefused("ambiguous-name", "ambiguous")
    return matches[0]


class TestFinding9SecretShapedNamesStayReachable(unittest.TestCase):
    """FINDING 9 (external audit 2026-07-27, HIGH): a thread whose NAME trips
    the redactor was stored fine and then became unreachable forever, its
    fence stranded, because _find_record resolved names through store.dump()
    (a PRESENTATION view whose default-deny redaction rewrites records.name).

    "password=hunter2" is a legal name: bm_store.valid_name rejects reserved
    characters, whitespace and non-printable ASCII, and nothing else. So this
    was reachable by typing, not only in theory."""

    SECRET_NAME = "password=hunter2"

    def test_the_name_really_does_trip_the_redactor(self):
        # The premise, asserted rather than assumed: if this ever stops being
        # true, every other test in this class would pass for the wrong
        # reason and prove nothing at all.
        self.assertEqual(bs.redact_text(self.SECRET_NAME), "[REDACTED]")
        self.assertEqual(bs.valid_name(self.SECRET_NAME), self.SECRET_NAME,
                         "the store must still accept this name, or the bug it "
                         "strands could not be created in the first place")

    def test_a_thread_named_like_a_secret_stays_reachable_for_its_whole_life(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            r = _run_threads(["start", self.SECRET_NAME, "ship it",
                              "--files", "api/a.py", "--session", "s1"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rows = _identity_rows(d, self.SECRET_NAME)
            self.assertEqual(len(rows), 1, "the record must have been stored under "
                                           "its real name: %s" % rows)
            luid = rows[0]["lifecycle_uuid"]
            # Every name-addressed command, not just the one the audit named.
            for args in (["checkpoint", self.SECRET_NAME, "--next", "wire it up"],
                         ["decide", self.SECRET_NAME, "--topic", "api", "--text", "chose sqlite"],
                         ["send", self.SECRET_NAME, "keep going"],
                         ["park", self.SECRET_NAME, "--session", "s1"],
                         ["resume", self.SECRET_NAME, "--session", "s1"],
                         ["complete", self.SECRET_NAME, "--session", "s1",
                          "--evidence", "tests pass: 12/12"]):
                r = _run_threads(args, d)
                self.assertEqual(r.returncode, 0,
                                 "`%s` could not reach a thread whose name trips the "
                                 "redactor; its fence would be stranded forever:\n%s\n%s"
                                 % (args[0], r.stdout, r.stderr))
            final = [row for row in _identity_rows(d, self.SECRET_NAME)
                     if row["lifecycle_uuid"] == luid]
            self.assertEqual(final[0]["state"], "complete")

    def test_the_secret_name_never_reaches_stdout_or_a_file_in_cleartext(self):
        # Resolution stopped going through the redactor, so this proves the
        # protection that was really wanted is still there, at the OUTPUT
        # boundary where it belongs.
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            r = _run_threads(["start", self.SECRET_NAME, "ship it",
                              "--files", "api/a.py", "--session", "s1"], d)
            self.assertNotIn(self.SECRET_NAME, r.stdout)
            _run_threads(["checkpoint", self.SECRET_NAME, "--next", "wire it up"], d)
            r_off = _run_threads(["off"], d)
            self.assertEqual(r_off.returncode, 0, r_off.stdout + r_off.stderr)
            self.assertNotIn(self.SECRET_NAME, r_off.stdout)
            state = _read(os.path.join(d, "STATE.md"))
            self.assertIn("Drained from thread mode", state,
                          "the handover must still have been delivered")
            self.assertNotIn(self.SECRET_NAME, state,
                             "reading the authoritative row must not have carried the "
                             "raw name into STATE.md; cmd_off protects it explicitly")
            mode = _read(os.path.join(d, "threads", "thread-mode.json"))
            self.assertNotIn(self.SECRET_NAME, mode,
                             "the drained-history list is written to disk and must "
                             "carry the protected name, not the raw one")
            parked = _identity_rows(d, self.SECRET_NAME)
            self.assertEqual([row["state"] for row in parked], ["parked"],
                             "`off` must have found and parked it by identity")

    def test_calibrated_the_old_dump_based_resolution_strands_the_thread(self):
        # REINJECTION: put the pre-fix resolver back and watch the same
        # thread become unfindable.
        with tempfile.TemporaryDirectory() as d:
            mod = _fresh_threads_module("finding9")
            _run_threads(["on"], d)
            _run_threads(["start", self.SECRET_NAME, "ship it",
                          "--files", "api/a.py", "--session", "s1"], d)
            store = bs.Store(d, create=False)
            try:
                found = mod._find_record(store, self.SECRET_NAME)
                self.assertIsNotNone(found["lifecycle_uuid"],
                                     "the FIXED resolver must find it")
                mod._find_record = _OLD_find_record_through_dump
                with self.assertRaises(bs.OwnershipRefused) as caught:
                    mod._find_record(store, self.SECRET_NAME)
                self.assertEqual(caught.exception.reason, "not-found",
                                 "the pre-fix resolver must fail to find a record "
                                 "that is plainly in the store, which is the whole "
                                 "of FINDING 9")
            finally:
                store.close()

    def test_no_record_resolution_in_bm_threads_reads_the_dump_view(self):
        # The structural half: dump() may still be used for the two GENERATED
        # VIEWS (inbox.md's directives, and the dashboard's own render), and
        # for enumerating lifecycle uuids in _records_by_identity, but never
        # to answer "which record is this name".
        src = io.open(os.path.join(HERE, "bm_threads.py"), encoding="utf-8").read()
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            if "dump()" not in code:
                continue
            self.assertNotIn("name", code,
                             "a dump() read is being matched against a name again "
                             "(FINDING 9): %r" % line.strip())
        self.assertIn("store.identity_by_name(name)", src,
                      "_find_record must resolve through the store's direct lookup")


class TestFinding10AmbiguousNamesAlwaysRefuse(unittest.TestCase):
    """FINDING 10 (external audit 2026-07-27, HIGH): _find_record promised in
    its own docstring that it never guesses, then guessed. It reported
    ambiguity ONLY when the top two updated_at strings were exactly EQUAL,
    and those are second-resolution timestamps, so in practice it silently
    acted on whichever lifecycle had been touched most recently."""

    def _two_parked_lives(self, d, pause=1.1):
        """Two records named 'alpha', both parked. The store's unique index
        forbids two ACTIVE records sharing a name, so a reused name is
        exactly how a founder ends up here: build a life, park it, build
        another, park that.

        `pause` separates the two lives in time, and DEFAULTS to more than a
        second for a reason worth stating: updated_at is a second-resolution
        string, and the pre-fix resolver only reported ambiguity when the top
        two were exactly EQUAL. Two records built back to back land in the
        same second, take that rare tie branch, and get refused even by the
        broken code, so a test built that way passes against the bug and
        proves nothing. Letting the clock tick is not scaffolding: it is the
        normal case, and it is why the silent guess was the everyday path."""
        import time as _time
        _run_threads(["on"], d)
        _run_threads(["start", "alpha", "first life", "--files", "api/a.py",
                      "--session", "s1"], d)
        _run_threads(["park", "alpha", "--session", "s1"], d)
        if pause:
            _time.sleep(pause)
        _run_threads(["start", "alpha", "second life", "--files", "api/b.py",
                      "--session", "s2"], d)
        _run_threads(["park", "alpha", "--session", "s2"], d)
        rows = _identity_rows(d, "alpha")
        self.assertEqual(len(rows), 2, "test setup failed: %s" % rows)
        self.assertEqual(sorted(r["state"] for r in rows), ["parked", "parked"])
        return [r["lifecycle_uuid"] for r in rows]

    def test_resume_refuses_and_names_every_candidate_and_the_flag(self):
        with tempfile.TemporaryDirectory() as d:
            uuids = self._two_parked_lives(d)
            r = _run_threads(["resume", "alpha", "--session", "s3"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("ambiguous-name", r.stdout)
            for u in uuids:
                self.assertIn(u, r.stdout,
                              "the refusal must list every candidate lifecycle uuid "
                              "so the founder can pick one: %s" % r.stdout)
            self.assertIn("--lifecycle", r.stdout,
                          "the refusal must name the exact flag that resolves it")
            self.assertEqual(sorted(x["state"] for x in _identity_rows(d, "alpha")),
                             ["parked", "parked"],
                             "a refusal must change nothing")

    def test_decide_and_adopt_inherit_the_same_refusal(self):
        # Not each carrying its own copy of the resolution logic: the same
        # reason string, from the same helper, for commands with completely
        # different state filters.
        with tempfile.TemporaryDirectory() as d:
            uuids = self._two_parked_lives(d)
            for args in (["decide", "alpha", "--topic", "api", "--text", "x"],
                         ["adopt", "alpha", "--session", "s4"]):
                r = _run_threads(args, d)
                self.assertEqual(r.returncode, 2, "%s: %s%s" % (args[0], r.stdout, r.stderr))
                self.assertIn("ambiguous-name", r.stdout, args[0])
                for u in uuids:
                    self.assertIn(u, r.stdout, args[0])

    def test_the_lifecycle_flag_is_a_working_escape_not_just_advice(self):
        with tempfile.TemporaryDirectory() as d:
            uuids = self._two_parked_lives(d)
            wanted = uuids[0]
            r = _run_threads(["resume", "alpha", "--session", "s3",
                              "--lifecycle", wanted[:8]], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            states = {row["lifecycle_uuid"]: row["state"] for row in _identity_rows(d, "alpha")}
            self.assertEqual(states[wanted], "active")
            self.assertEqual(states[uuids[1]], "parked",
                             "only the named lifecycle may move")

    def test_calibrated_the_old_newest_wins_shortcut_guessed_silently(self):
        # REINJECTION: the pre-fix resolver, on the very input the fixed one
        # refuses, returns a record and says nothing.
        with tempfile.TemporaryDirectory() as d:
            mod = _fresh_threads_module("finding10")
            uuids = self._two_parked_lives(d, pause=1.1)
            rows = _identity_rows(d, "alpha")
            self.assertNotEqual(rows[0]["updated_at"], rows[1]["updated_at"],
                                "test setup failed: the two lives must have "
                                "DIFFERENT updated_at stamps, or the pre-fix "
                                "resolver takes its rare tie branch and this "
                                "calibration proves nothing")
            store = bs.Store(d, create=False)
            try:
                # bm_threads loads its OWN private copy of bm_store, so the
                # class it raises is not this test module's bs.OwnershipRefused.
                with self.assertRaises(mod._store().OwnershipRefused) as caught:
                    mod._find_record(store, "alpha", states=("parked",))
                self.assertEqual(caught.exception.reason, "ambiguous-name")
                mod._find_record = _OLD_find_record_through_dump
                guessed = mod._find_record(store, "alpha", states=("parked",))
                self.assertIn(guessed["lifecycle_uuid"], uuids,
                              "the pre-fix resolver silently picked one of two "
                              "lifecycles, which is exactly FINDING 10")
            finally:
                store.close()

    def test_name_resolution_has_exactly_one_owner(self):
        # The architectural half of "verify that resume, decide, complete and
        # adopt all inherit the refusal": every command reaches records by
        # name through _find_record, so there is one place the policy can be
        # weakened, not six.
        src = io.open(os.path.join(HERE, "bm_threads.py"), encoding="utf-8").read()
        callers = [ln.strip() for ln in src.splitlines()
                   if "identity_by_name(" in ln.split("#", 1)[0]]
        self.assertEqual(len(callers), 1,
                         "exactly one function may resolve a name to a record "
                         "(_find_record); found: %s" % callers)
        for command in ("cmd_checkpoint", "cmd_decide", "cmd_send",
                        "_transition_cmd", "cmd_adopt"):
            body = src.split("def %s(" % command, 1)[1].split("\ndef ", 1)[0]
            self.assertIn("_find_record(", body,
                          "%s must resolve names through the shared refusal, not "
                          "its own copy" % command)


class TestPostAuditLoopP12TransactionalHandovers(unittest.TestCase):
    """LOOP P12. FINDING 12's lock is deleted and so is the append it guarded.

    THE DEFECT, reproduced against a real store on 2026-07-29 before the
    change: bm_threads appended a handover to the project root STATE.md while
    bm_store.write_state_view independently read, rebuilt and REPLACED the same
    file taking no lock at all. Park the record, deliver the handover, let the
    replace land on its earlier snapshot, and the record reads 'parked' with
    the handover text gone from disk and no second copy anywhere.

    THE FIX: a handovers table, and the row is INSERTed inside the same sqlite
    transaction as the lifecycle transition that produced it. STATE.md becomes
    a pure render of undelivered rows. These tests are the plan's required
    list: transition-commit failure leaves no handover, handover-insert failure
    leaves no transition, render failure preserves database truth, retry after
    a render failure does not duplicate, refused adoption changes nothing and
    writes nothing, and concurrent handovers serialize through the store."""

    def _thread(self, d, name="alpha", session="s1"):
        _run_threads(["on"], d)
        _run_threads(["start", name, "ship it", "--files", "api/%s.py" % name,
                      "--session", session], d)
        _run_threads(["checkpoint", name, "--next", "wire the webhook"], d)
        return _identity_rows(d, name)[0]

    def _handover_rows(self, root, undelivered_only=False):
        store = bs.Store(root, create=False)
        try:
            if undelivered_only:
                return [dict(r) for r in store.undelivered_handovers()]
            return [dict(r) for r in bs._exec(
                store, "SELECT * FROM handovers ORDER BY rowid").fetchall()]
        finally:
            store.close()

    def test_a_park_and_its_handover_commit_together(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", note="drained",
                                  handover_heading="Drained: alpha")
            finally:
                store.close()
            rows = self._handover_rows(d)
            self.assertEqual(len(rows), 1, rows)
            self.assertEqual(rows[0]["lifecycle_uuid"], rec["lifecycle_uuid"])
            self.assertIsNone(rows[0]["delivered_at"],
                              "a fresh handover is undelivered until acknowledged")
            self.assertIn("wire the webhook", rows[0]["body"])
            self.assertIsNotNone(rows[0]["transition_id"],
                                 "the handover must name the transition it committed with")

    def test_a_refused_transition_leaves_no_handover(self):
        # "Transition commit failure leaves no handover." The refusal used
        # here is the real one a founder hits: adopting a record that is still
        # ACTIVE under a different live session, without the explicit flag.
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d, session="sessA")
            store = bs.Store(d, create=False)
            try:
                with self.assertRaises(bs.OwnershipRefused):
                    store.transition(rec["lifecycle_uuid"], rec["version"], "adopted",
                                      session_id="sessB",
                                      handover_heading="Adopted: alpha")
            finally:
                store.close()
            self.assertEqual(self._handover_rows(d), [],
                             "a refused transition wrote a handover anyway")
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["active"])

    def test_a_failing_handover_insert_leaves_no_transition(self):
        # "Handover insert failure leaves no transition." _insert_handover is
        # its own named symbol precisely so this can be reinjected onto it.
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            real = bs.Store._insert_handover
            transitions_before = len(bs._exec(
                store, "SELECT * FROM transitions").fetchall())

            def boom(self_store, *a, **k):
                raise bs.RedactionUnavailable(
                    "redaction-unavailable", "injected: the redactor is gone")
            bs.Store._insert_handover = boom
            try:
                with self.assertRaises(bs.RedactionUnavailable):
                    store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                      session_id="s1",
                                      handover_heading="Drained: alpha")
            finally:
                bs.Store._insert_handover = real
                store.close()
            self.assertEqual(self._handover_rows(d), [])
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["active"],
                             "the record moved even though its handover was never written")
            store = bs.Store(d, create=False)
            try:
                after = len(bs._exec(store, "SELECT * FROM transitions").fetchall())
            finally:
                store.close()
            self.assertEqual(after, transitions_before,
                             "the transitions row survived a rolled-back handover")

    def test_calibrated_a_two_step_deliver_after_commit_splits_the_pair(self):
        # REINJECTION: do it the way every version before this loop did it,
        # as two steps, and let the second step fail. The record moves and the
        # handover does not exist: exactly the split state this loop closes.
        # If this stops reproducing, the test above is not calibrated.
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", note="drained")
                # step two, outside the transaction, fails
                raised = False
                try:
                    raise OSError("injected: the handover write failed")
                except OSError:
                    raised = True
                self.assertTrue(raised)
            finally:
                store.close()
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["parked"],
                             "REINJECTION CHECK: the old two-step order must still move "
                             "the record")
            self.assertEqual(
                self._handover_rows(d), [],
                "REINJECTION CHECK: with delivery as a SECOND step, a parked record "
                "with no handover at all must still be reachable; if this fails, the "
                "atomicity tests above are not calibrated to the defect they claim")

    def test_render_failure_preserves_database_truth_and_regenerates(self):
        # "Render failure preserves database truth" and "crash after commit
        # but before render is recoverable by regeneration."
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
            finally:
                store.close()
            # The crash: STATE.md never gets written, or is destroyed after it was.
            state_path = os.path.join(d, "STATE.md")
            if os.path.exists(state_path):
                os.remove(state_path)
            self.assertEqual(len(self._handover_rows(d, undelivered_only=True)), 1,
                             "the store must still hold the handover after a render crash")
            bs.write_state_view(d)
            self.assertIn("Drained: alpha", _read(state_path),
                          "regeneration must put the handover back")
            self.assertIn("wire the webhook", _read(state_path))

    def test_retry_after_a_render_failure_does_not_duplicate(self):
        # "Retry cannot duplicate text." The retry here is the honest one: the
        # same handover asked for twice against the same lifecycle.
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
                back = _identity_rows(d, "alpha")[0]
                store.transition(back["lifecycle_uuid"], back["version"], "active",
                                  session_id="s1")
                again = _identity_rows(d, "alpha")[0]
                store.transition(again["lifecycle_uuid"], again["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
            finally:
                store.close()
            rows = self._handover_rows(d)
            self.assertEqual(len(rows), 1,
                             "the same handover was stored twice: %s"
                             % [r["payload_fingerprint"] for r in rows])
            bs.write_state_view(d)
            self.assertEqual(_read(os.path.join(d, "STATE.md")).count("Drained: alpha"), 1,
                             "the retried handover was rendered twice")
            self.assertIsNone(rows[0]["delivered_at"],
                              "the one surviving row must be the UNDELIVERED one: the "
                              "dedupe is only honest while the founder can still see it")

    # -- fix round, 2026-07-29: the dedupe that deleted handovers -----------
    #
    # The three tests below are the fix for the P12 fix-round findings. The
    # dedupe index was UNIQUE(lifecycle_uuid, payload_fingerprint) over ALL
    # rows for ALL time, and _insert_handover swallowed its IntegrityError
    # unconditionally while transition() discarded the result. handover_payload
    # fingerprints objective, files, owner, tier, check, evidence, latest digest
    # and decisions, and NOT state, version, transition_id or heading, so a
    # second park or an adopt with an unchanged payload wrote nothing at all.

    def _park_ack_resume(self, d, heading="Drained: alpha"):
        """Park with a handover, acknowledge it, resume. Leaves the record
        ACTIVE with one DELIVERED handover behind it, which is the state that
        used to make the next park's handover vanish."""
        rec = _identity_rows(d, "alpha")[0]
        store = bs.Store(d, create=False)
        try:
            store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                              session_id="s1", handover_heading=heading)
            store.acknowledge_handover(self._handover_rows(d)[0]["handover_uuid"])
            back = _identity_rows(d, "alpha")[0]
            store.transition(back["lifecycle_uuid"], back["version"], "active",
                              session_id="s1")
        finally:
            store.close()
        return _identity_rows(d, "alpha")[0]

    def test_a_park_after_an_acknowledged_handover_still_writes_one(self):
        # THE FINDING: park, acknowledge, resume, park again with the payload
        # unchanged. The second park committed with NO handover row anywhere:
        # `handovers` said none, STATE.md had no section, `verify` said healthy.
        with tempfile.TemporaryDirectory() as d:
            self._thread(d)
            again = self._park_ack_resume(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(again["lifecycle_uuid"], again["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
            finally:
                store.close()
            rows = self._handover_rows(d)
            self.assertEqual(len(rows), 2,
                             "the second park lost its handover to the first one's "
                             "fingerprint: %s" % [r["heading"] for r in rows])
            undelivered = self._handover_rows(d, undelivered_only=True)
            self.assertEqual(len(undelivered), 1, undelivered)
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["parked"])
            bs.write_state_view(d)
            self.assertIn("Drained: alpha", _read(os.path.join(d, "STATE.md")),
                          "a parked record's handover must render again")

    def test_calibrated_the_all_time_fingerprint_index_loses_that_handover(self):
        # REINJECTION: put the schema-5 index back, exactly as it shipped, and
        # run the same sequence. The record parks and no undelivered handover
        # exists. If this stops reproducing, the test above proves nothing.
        with tempfile.TemporaryDirectory() as d:
            self._thread(d)
            again = self._park_ack_resume(d)
            store = bs.Store(d, create=False)
            try:
                store.conn.execute("DROP INDEX handovers_undelivered_text_idx")
                store.conn.execute(
                    "CREATE UNIQUE INDEX handovers_lifecycle_fingerprint_idx "
                    "ON handovers(lifecycle_uuid, payload_fingerprint)")
                # The old swallow, restored for the length of this call: any
                # IntegrityError becomes "already" and the caller never hears.
                real = bs.Store._insert_handover

                def old_swallow(self, lifecycle_uuid, transition_id, heading,
                                from_session_id="", to_session_id="", at=None):
                    try:
                        return real(self, lifecycle_uuid, transition_id, heading,
                                    from_session_id=from_session_id,
                                    to_session_id=to_session_id, at=at)
                    except bs.HandoverLost:
                        return "already"

                bs.Store._insert_handover = old_swallow
                try:
                    store.transition(again["lifecycle_uuid"], again["version"], "parked",
                                      session_id="s1", handover_heading="Drained: alpha")
                finally:
                    bs.Store._insert_handover = real
            finally:
                store.close()
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["parked"],
                             "REINJECTION CHECK: the old shape must still park the record")
            self.assertEqual(
                self._handover_rows(d, undelivered_only=True), [],
                "REINJECTION CHECK: with the schema-5 index and the old swallow, a "
                "parked record with no visible handover must still be reachable; if "
                "this fails, the test above is not calibrated to the defect it claims")

    def test_a_swallow_with_nothing_left_to_render_rolls_the_transition_back(self):
        # The guard behind the index: even with the schema-5 index put back,
        # _insert_handover now refuses to swallow when no UNDELIVERED twin
        # exists, and the transition rolls back with it. The record does not
        # move, so "parked with no handover" is unreachable twice over.
        with tempfile.TemporaryDirectory() as d:
            self._thread(d)
            again = self._park_ack_resume(d)
            store = bs.Store(d, create=False)
            try:
                store.conn.execute("DROP INDEX handovers_undelivered_text_idx")
                store.conn.execute(
                    "CREATE UNIQUE INDEX handovers_lifecycle_fingerprint_idx "
                    "ON handovers(lifecycle_uuid, payload_fingerprint)")
                with self.assertRaises(bs.HandoverLost) as caught:
                    store.transition(again["lifecycle_uuid"], again["version"], "parked",
                                      session_id="s1", handover_heading="Drained: alpha")
                self.assertEqual(caught.exception.reason, "handover-lost")
            finally:
                store.close()
            self.assertEqual([r["state"] for r in _identity_rows(d, "alpha")], ["active"],
                             "the transition committed without its handover")
            self.assertEqual(self._handover_rows(d, undelivered_only=True), [])

    def test_an_adoption_after_a_park_renders_its_own_heading(self):
        # THE SECOND FINDING: adopt right after `threads off`. The payload has
        # not changed since the park (adopt writes no digest of its own), so
        # the adoption's handover lost on the fingerprint and STATE.md kept
        # rendering the park's heading, and a body reading 'parked', for a
        # record the store had already moved to 'adopted'.
        with tempfile.TemporaryDirectory() as d:
            self._thread(d, name="gamma", session="s9")
            _run_threads(["off"], d)
            r = _run_threads(["adopt", "gamma", "--session", "chief"], d)
            self.assertEqual(r.returncode, 0, r.stdout)
            headings = [row["heading"] for row in self._handover_rows(d)]
            self.assertEqual(len(headings), 2, headings)
            self.assertTrue(any(h.startswith("Adopted from") for h in headings),
                            "the adoption has no handover of its own: %s" % headings)
            state = _read(os.path.join(d, "STATE.md"))
            self.assertIn("Adopted from dead/stalled thread: gamma", state,
                          "the CLI told the founder the adoption handover renders, and "
                          "it does not")
            self.assertEqual([row["state"] for row in _identity_rows(d, "gamma")],
                             ["adopted"])

    def test_a_refused_adoption_writes_nothing_at_all(self):
        # "Refused adoption changes no state and writes no handover."
        with tempfile.TemporaryDirectory() as d:
            self._thread(d, name="payments", session="sessA")
            _run_threads(["dashboard"], d)
            before = _read(os.path.join(d, "STATE.md"))
            r = _run_threads(["adopt", "payments"], d)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("ADOPT REFUSED (live-session-adopt-blocked)", r.stdout)
            self.assertEqual(self._handover_rows(d), [],
                             "a refused adoption wrote a handover")
            self.assertEqual(before, _read(os.path.join(d, "STATE.md")),
                             "a refused adoption must leave STATE.md byte-for-byte unchanged")
            self.assertEqual([r["state"] for r in _identity_rows(d, "payments")], ["active"])

    def test_concurrent_handovers_serialize_through_the_store(self):
        # "Concurrent handovers serialize through SQLite transaction." Two
        # processes race the SAME park; exactly one wins the transition, and
        # exactly one handover exists either way.
        import threading
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            outcomes, lock = [], threading.Lock()

            def park():
                store = bs.Store(d, create=False)
                try:
                    store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                      session_id="s1", handover_heading="Drained: alpha")
                    result = "won"
                except (bs.StaleIdentity, bs.OwnershipRefused) as e:
                    result = type(e).__name__
                finally:
                    store.close()
                with lock:
                    outcomes.append(result)

            workers = [threading.Thread(target=park) for _ in range(2)]
            for w in workers:
                w.start()
            for w in workers:
                w.join(timeout=30)
            for w in workers:
                self.assertFalse(w.is_alive(), "a park thread never finished")
            self.assertEqual(outcomes.count("won"), 1,
                             "both parks committed: %s" % outcomes)
            self.assertEqual(len(self._handover_rows(d)), 1,
                             "a concurrent pair wrote more than one handover")

    def test_acknowledging_is_idempotent_and_stops_the_rendering(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self._thread(d)
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
                huid = self._handover_rows(d)[0]["handover_uuid"]
                self.assertEqual(store.acknowledge_handover(huid), "delivered")
                self.assertEqual(store.acknowledge_handover(huid), "already",
                                  "a second acknowledgement must change nothing")
                with self.assertRaises(bs.StaleIdentity):
                    store.acknowledge_handover("no-such-handover")
            finally:
                store.close()
            bs.write_state_view(d)
            self.assertNotIn("Drained: alpha", _read(os.path.join(d, "STATE.md")),
                             "an acknowledged handover must stop rendering")
            self.assertEqual(len(self._handover_rows(d)), 1,
                             "acknowledging must never delete the row")

    def test_the_handover_body_is_redacted_before_it_reaches_the_view(self):
        with tempfile.TemporaryDirectory() as d:
            _run_threads(["on"], d)
            _run_threads(["start", "alpha", "ship it", "--files", "api/a.py",
                          "--session", "s1"], d)
            _run_threads(["checkpoint", "alpha", "--next", "wire it",
                          "--topic", "notes", "--decision",
                          "deploy key AKIAIOSFODNN7EXAMPLE password=hunter2swordfish"], d)
            rec = _identity_rows(d, "alpha")[0]
            store = bs.Store(d, create=False)
            try:
                store.transition(rec["lifecycle_uuid"], rec["version"], "parked",
                                  session_id="s1", handover_heading="Drained: alpha")
            finally:
                store.close()
            bs.write_state_view(d)
            state = _read(os.path.join(d, "STATE.md"))
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", state)
            self.assertNotIn("hunter2swordfish", state)
            self.assertIn("[REDACTED]", state)
            self.assertIn("wire it", state, "redaction destroyed the real content")

    def test_the_rendered_body_keeps_its_line_structure(self):
        # A handover body is a rendered DOCUMENT: running the per-FIELD view
        # pipeline over it collapsed every newline into a literal \x0a
        # (reproduced in this loop's probe). _redacted_view_block exists for
        # exactly this, and every OTHER control character must still escape.
        self.assertEqual(bs._redacted_view_block("a\nb"), "a\nb")
        self.assertEqual(bs._redacted_view_block("a\x1b[2Kb"), "a\\x1b[2Kb")
        self.assertIn("\n", bs._redacted_view_block("## Next intent\n\nship it"))

    def test_the_old_appended_handovers_are_left_as_prose(self):
        # The migration rule: old appended handovers stay human prose and are
        # never parsed back in. Anything outside the generated markers must
        # survive a regeneration untouched.
        with tempfile.TemporaryDirectory() as d:
            self._thread(d)
            legacy = ("# Project STATE\n\n<!-- brothermode-handover:old:abc -->\n"
                      "## Drained from thread mode: legacy\nkeep me\n")
            io.open(os.path.join(d, "STATE.md"), "w", encoding="utf-8").write(legacy)
            bs.write_state_view(d)
            state = _read(os.path.join(d, "STATE.md"))
            self.assertIn("<!-- brothermode-handover:old:abc -->", state,
                          "an old appended handover was destroyed by regeneration")
            self.assertIn("keep me", state)
            self.assertEqual(self._handover_rows(d), [],
                             "an old appended handover was parsed back into the store")

    def test_the_append_path_and_its_lock_are_gone(self):
        src = io.open(os.path.join(HERE, "bm_threads.py"), encoding="utf-8").read()
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        for symbol in ("def _deliver_handover_once", "def _handover_tag",
                       "def _handover_landed", "class _StateFileLock",
                       "def _state_lock", "STATE_LOCK_WAIT_SECONDS"):
            self.assertNotIn(symbol, code,
                             "%s must be DELETED, not shimmed (LOOP P12)" % symbol)


_learning_spec = importlib.util.spec_from_file_location(
    "bm_learning", os.path.join(HERE, "bm_learning.py"))
bl = importlib.util.module_from_spec(_learning_spec)
_learning_spec.loader.exec_module(bl)


class TestLoop4CorrectionDetection(unittest.TestCase):
    """docs/NOT-FINALIZED.md item 17, closed. The shipped filter was one English
    regex with a hard 400-character cap: of five real founder messages it
    captured two, dropping a FRENCH correction and a long one, both silently.
    The founder works in French, so that was not an edge case."""

    FR = ("Non, ce n'est pas ce que je voulais. Desormais utilise toujours "
          "l'application GitHub Desktop pour pousser.")
    JA = "違います。今後は必ずGitHub Desktopを使ってください。"
    EN = "No, that is not what I asked for. From now on use the desktop app."

    def test_the_old_english_regex_still_captures_what_it_used_to(self):
        # The regex is preserved as ONE signal rather than replaced, so recall
        # can only go up. If this fails, the rewrite lost coverage.
        hit = bl.detect_correction(self.EN)
        self.assertIsNotNone(hit)
        self.assertIn("en:legacy-en-regex", hit["signals"])

    def test_a_french_correction_is_captured(self):
        hit = bl.detect_correction(self.FR)
        self.assertIsNotNone(hit, "the founder works in French; this is the item 17 case")
        self.assertEqual(hit["lang"], "fr")

    def test_a_japanese_correction_is_captured(self):
        hit = bl.detect_correction(self.JA)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["lang"], "ja")

    def test_a_long_correction_is_excerpted_not_dropped(self):
        long_text = ("No, that is wrong. " + ("lead with customer impact. " * 200)
                     + " From now on always lead with the business state.")
        self.assertGreater(len(long_text), 4000)
        hit = bl.detect_correction(long_text)
        self.assertIsNotNone(hit, "a long correction must never be dropped")
        self.assertTrue(hit["truncated"])
        self.assertEqual(hit["original_length"], len(bl.normalize_text(long_text)))
        self.assertLessEqual(len(hit["excerpt"]), bl.CORRECTION_EXCERPT_LIMIT)
        self.assertIn("characters omitted", hit["excerpt"],
                      "the cut must be visible to the reviewer, never silent")
        self.assertIn("business state", hit["excerpt"],
                      "the instruction usually sits at the END; keeping only the head "
                      "is how the old cap destroyed the useful half")

    def test_quoted_law_text_is_not_a_founder_correction(self):
        quoted = ("> from now on always use the desktop app\n"
                  "> never do a bare git push")
        self.assertIsNone(bl.detect_correction(quoted))
        self.assertIsNone(bl.detect_correction(
            "SKILL.md says never do a bare git push, from now on."))

    def test_named_negative_fixtures_do_not_flood_the_queue(self):
        for text, why in (
                ("why didn't you use the desktop app?", "a question, not a standing order"),
                ("What if we always use a different push flow?", "brainstorming"),
                ("We decided to change the plan, never do the old flow",
                 "a changed business decision"),
                ("the weather is nice today", "no correction shape at all")):
            self.assertIsNone(bl.detect_correction(text), "%s: %s" % (why, text))

    def test_a_question_carrying_a_standing_instruction_is_still_captured(self):
        # The interrogative rule must not swallow a real correction that ends
        # in a question mark, which is how the founder often phrases them.
        self.assertIsNotNone(bl.detect_correction("from now on use the desktop app, ok?"))

    def test_inbox_identity_is_stable_and_session_scoped(self):
        a = bl.inbox_identity("s1", "  No,  that is   wrong ")
        self.assertEqual(a, bl.inbox_identity("s1", "No, that is wrong"),
                         "identity must survive whitespace, or a re-import duplicates")
        self.assertNotEqual(a, bl.inbox_identity("s2", "No, that is wrong"))


def _consented_env(repo, vault):
    """Env for driving bm_telemetry.py subprocesses now that the Loop 3
    consent gate exists: without a consented config the hook rightly
    writes nothing, which is its own tested behavior, not these tests'
    subject. Writes a completed consent config into the test's own temp
    repo and points BROTHERME_CONFIG at it."""
    cfg_path = os.path.join(repo, "consent.json")
    with io.open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"setup_complete": True, "vault_path": vault,
                   "privacy_notice_version": "2026-08-01",
                   "installation_mode": "clone",
                   "security_mode": "standard"}, f)
    return dict(os.environ, BROTHERMODE_VAULT=vault,
                BROTHERME_CONFIG=cfg_path)


class TestLoop4CaptureThroughTheRealHook(unittest.TestCase):
    """The SessionEnd path end to end, through the real CLI, because in this
    project every serious defect so far was found by driving the binary while
    the suite was green."""

    def _run(self, vault, msgs):
        repo = tempfile.mkdtemp()
        tp = os.path.join(repo, "transcript.jsonl")
        lines = [{"type": "user", "timestamp": "2026-07-29T10:0%d:00Z" % i,
                  "message": {"content": m}} for i, m in enumerate(msgs)]
        for i in range(6):
            lines.append({"type": "assistant", "timestamp": "2026-07-29T10:1%d:00Z" % i,
                          "message": {"id": "m%d" % i, "model": "opus",
                                      "usage": {"output_tokens": 10, "input_tokens": 10},
                                      "content": [{"type": "tool_use", "name": "Bash"}]}})
        with io.open(tp, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(o) for o in lines) + "\n")
        payload = json.dumps({"transcript_path": tp, "session_id": "sess-loop4",
                              "cwd": repo, "reason": "other"})
        subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),  # sbe: allow-silent outcomes-append fixture call whose effect is verified by reading corrections.jsonl afterward
                        "outcomes-append"],
                       input=payload, env=_consented_env(repo, vault),
                       cwd=repo, capture_output=True, text=True)
        p = os.path.join(vault, "99-System", "telemetry", "corrections.jsonl")
        rows = []
        if os.path.exists(p):
            rows = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
        return p, rows

    def test_french_and_long_corrections_reach_the_file_owner_only(self):
        long_text = "No, that is wrong. " + ("x " * 3000) + " From now on always ask first."
        with tempfile.TemporaryDirectory() as v:
            p, rows = self._run(v, [TestLoop4CorrectionDetection.FR, long_text,
                                    TestLoop4CorrectionDetection.JA,
                                    "why didn't you use the desktop app?"])
            self.assertEqual(sorted(r.get("lang") for r in rows), ["en", "fr", "ja"],
                             "French and Japanese must survive the hook, and the "
                             "question must not be captured")
            long_rows = [r for r in rows if r.get("truncated")]
            self.assertEqual(len(long_rows), 1)
            self.assertGreater(long_rows[0]["original_length"], 4000)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600,
                             "captured founder text stays owner-only")

    def test_secret_shaped_content_is_redacted_before_it_is_written(self):
        with tempfile.TemporaryDirectory() as v:
            _p, rows = self._run(v, ["No, that is wrong. From now on use "
                                     "AKIAIOSFODNN7EXAMPLE for the upload."])
            self.assertEqual(len(rows), 1)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", rows[0]["text"])
            self.assertIn("[REDACTED]", rows[0]["text"])

    def test_a_duplicate_flush_does_not_duplicate_candidates(self):
        with tempfile.TemporaryDirectory() as v:
            self._run(v, [TestLoop4CorrectionDetection.FR])
            _p, rows = self._run(v, [TestLoop4CorrectionDetection.FR])
            self.assertEqual(len(rows), 1,
                             "the SessionEnd hook fires more than once per session")

    def test_capture_never_creates_an_approved_rule(self):
        """The whole point of this loop: recall goes up, authority does not."""
        src = io.open(os.path.join(HERE, "bm_telemetry.py"), encoding="utf-8").read()
        for forbidden in ("approve_learning_candidate", "learning_rules"):
            self.assertNotIn(forbidden, src,
                             "no automatic path may reach rule creation (%s)" % forbidden)


class TestLoop4PairingAndFileDedup(unittest.TestCase):
    """The correction FILE, driven through the real SessionEnd hook: what a row
    carries about the exchange it came from, and the guard that says a row
    already in the file is never re-appended."""

    MULTILINE = "No, that is wrong.\nFrom now on always ask first."

    def _run(self, vault, msgs, session="sess-pair", said="", artifact=""):
        repo = tempfile.mkdtemp()
        tp = os.path.join(repo, "transcript.jsonl")
        lines = []
        for i in range(6):
            content = [{"type": "tool_use", "name": "Write",
                        "input": {"file_path": artifact} if artifact else {}}]
            if said and i == 5:
                content.insert(0, {"type": "text", "text": said})
            lines.append({"type": "assistant", "timestamp": "2026-07-29T10:1%d:00Z" % i,
                          "message": {"id": "m%d" % i, "model": "opus",
                                      "usage": {"output_tokens": 10, "input_tokens": 10},
                                      "content": content}})
        for i, m in enumerate(msgs):
            lines.append({"type": "user", "timestamp": "2026-07-29T10:2%d:00Z" % i,
                          "message": {"content": m}})
        with io.open(tp, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(o) for o in lines) + "\n")
        payload = json.dumps({"transcript_path": tp, "session_id": session,
                              "cwd": repo, "reason": "other"})
        subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),  # sbe: allow-silent outcomes-append fixture call whose effect is verified by reading corrections.jsonl afterward
                        "outcomes-append"],
                       input=payload, env=_consented_env(repo, vault),
                       cwd=repo, capture_output=True, text=True)
        p = os.path.join(vault, "99-System", "telemetry", "corrections.jsonl")
        rows = []
        if os.path.exists(p):
            rows = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
        return rows

    def _seed(self, vault, session, text):
        d = os.path.join(vault, "99-System", "telemetry")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "corrections.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-07-28T10:00:00Z", "session_id": session,
                                "project": "R", "text": text}) + "\n")

    def test_a_row_written_before_loop_4_is_not_re_appended(self):
        """The dedup set was built from the file's RAW text and compared against
        the detector's NORMALIZED excerpt, so every multi-line correction
        captured before Loop 4 came back on the next flush of that session."""
        with tempfile.TemporaryDirectory() as v:
            self._seed(v, "sess-old", self.MULTILINE)
            rows = self._run(v, [self.MULTILINE], session="sess-old")
            self.assertEqual(len(rows), 1,
                             "a (session, text) already in the file is never "
                             "re-appended, whatever whitespace it was stored with")

    def test_a_junk_row_in_the_file_does_not_take_the_hook_down(self):
        """The file is hand editable and the hook may never block work. A row
        that is not an object, or whose text is not a string, is ignored."""
        with tempfile.TemporaryDirectory() as v:
            d = os.path.join(v, "99-System", "telemetry")
            os.makedirs(d, exist_ok=True)
            with io.open(os.path.join(d, "corrections.jsonl"), "w",
                         encoding="utf-8") as f:
                f.write('"a bare string"\n')
                f.write(json.dumps({"session_id": "sess-junk", "text": 123}) + "\n")
            rows = self._run(v, [self.MULTILINE], session="sess-junk")
            self.assertEqual(len(rows), 3, "the real correction is still captured")
            self.assertEqual(rows[-1]["session_id"], "sess-junk")

    def test_a_row_carries_the_response_it_answers_and_the_artifact(self):
        with tempfile.TemporaryDirectory() as v:
            rows = self._run(v, [self.MULTILINE], session="sess-pair",
                             said="I pushed it with a bare git push.",
                             artifact="docs/report.md")
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(len(row["prev_response_hash"]), 16,
                             "the response is pinned by hash, never persisted whole")
            self.assertIn("bare git push", row["prev_response_excerpt"])
            self.assertEqual(row["artifacts"], ["docs/report.md"])

    def test_the_paired_excerpt_is_redacted_like_every_other_captured_text(self):
        with tempfile.TemporaryDirectory() as v:
            rows = self._run(v, [self.MULTILINE], session="sess-secret",
                             said="I used AKIAIOSFODNN7EXAMPLE for the upload.")
            self.assertEqual(len(rows), 1)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", rows[0]["prev_response_excerpt"])
            self.assertIn("[REDACTED]", rows[0]["prev_response_excerpt"])

    def test_a_long_response_excerpt_is_bounded_and_says_so(self):
        with tempfile.TemporaryDirectory() as v:
            rows = self._run(v, [self.MULTILINE], session="sess-long",
                             said="detail " * 400)
            self.assertEqual(len(rows), 1)
            self.assertLess(len(rows[0]["prev_response_excerpt"]), 400)
            self.assertIn("characters omitted", rows[0]["prev_response_excerpt"])


class TestLoop4FalsePositiveCategories(unittest.TestCase):
    """Rejection reasons bucketed for the capture metrics. The founder's own
    words decide the bucket; nothing is inferred about what he meant."""

    def test_known_reasons_land_in_their_bucket(self):
        for reason, want in (
                ("this was a one-off, not a standing rule", "one-off"),
                ("duplicate of the rule we already have", "duplicate"),
                ("that was just a question, not a correction", "not-a-correction"),
                ("wrong project, that belongs to the other repo", "wrong-scope"),
                ("I changed my mind about this", "superseded"),
                ("noise from the detector", "noise")):
            self.assertEqual(bl.false_positive_category(reason), want, reason)

    def test_an_unrecognized_or_empty_reason_is_other(self):
        self.assertEqual(bl.false_positive_category("mumble mumble"), "other")
        self.assertEqual(bl.false_positive_category(""), "other")
        self.assertEqual(bl.false_positive_category(None), "other")

    def test_every_category_is_declared(self):
        for name in ("one-off", "duplicate", "other"):
            self.assertIn(name, bl.FALSE_POSITIVE_CATEGORIES)

    def test_the_inbox_cli_survives_a_line_that_is_not_a_row(self):
        """Driven through the real binary, because that is where the traceback
        was: read_jsonl returns any valid JSON value, so a bare string reached
        r.get() and the founder got an AttributeError instead of an answer."""
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, BROTHERMODE_ROOT=root)
            init = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                                   "init"], cwd=root, env=env,
                                  capture_output=True, text=True)
            self.assertEqual(init.returncode, 0, init.stderr)
            bad = os.path.join(root, "bad.jsonl")
            with io.open(bad, "w", encoding="utf-8") as f:
                f.write('"just a bare string"\n123\n')
                f.write(json.dumps({"ts": "t", "session_id": "s1", "project": "P",
                                    "text": "No, that is wrong. From now on ask "
                                            "first."}) + "\n")
            for args in (["inbox", "--file", bad], ["inbox", "--file", bad, "--backfill"]):
                r = subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                                   + args, cwd=root, env=env, capture_output=True,
                                   text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("2 line(s) parsed but were not a row object", r.stdout)

    def test_an_echo_key_folds_case_and_spacing(self):
        self.assertEqual(bl.text_echo_key("No, that is  wrong"),
                         bl.text_echo_key("no, THAT is wrong"))
        self.assertNotEqual(bl.text_echo_key("no, that is wrong"),
                            bl.text_echo_key("no, that is fine"))


class TestLoop6ConflictSemantics(unittest.TestCase):
    """The pure half of Loop 6. These functions decide whether an approval is
    blocked, so what they can and cannot see is a stated property, not a
    hopeful one."""

    ALWAYS = "always push through the GitHub Desktop app"
    NEVER = "never push through the GitHub Desktop app"

    def test_polarity_reads_english_and_french_negation(self):
        self.assertEqual(bl.polarity(self.ALWAYS), "require")
        self.assertEqual(bl.polarity(self.NEVER), "forbid")
        self.assertEqual(bl.polarity("ne pousse jamais sans l'application"), "forbid")
        self.assertEqual(bl.polarity("utilise toujours l'application"), "require")

    def test_a_reversal_of_the_same_instruction_is_incompatible(self):
        self.assertEqual(bl.action_relation(self.ALWAYS, self.NEVER), "incompatible")
        self.assertEqual(bl.action_relation(self.ALWAYS, self.ALWAYS), "identical")

    def test_two_unrelated_actions_are_not_forced_into_a_verdict(self):
        self.assertEqual(
            bl.action_relation(self.ALWAYS, "write the summary in French"),
            "not_comparable")

    PADDED_NEVER = ("never push through the GitHub Desktop app, use the plain "
                    "command line instead every single time without exception")

    def test_a_padded_reversal_is_still_a_reversal(self):
        """The defect this closes: symmetric overlap punished length. Padding
        the reversal with an ordinary founder clause dropped the score under
        INCOMPATIBLE_FLOOR, the pair came back 'not_comparable', and two
        opposite instructions were free to sit side by side in the injectable
        set with nothing anywhere saying they disagreed."""
        self.assertLess(bl._jaccard(bl._content_tokens(self.ALWAYS),
                                    bl._content_tokens(self.PADDED_NEVER)),
                        bl.INCOMPATIBLE_FLOOR,
                        "symmetric overlap alone still cannot see this one")
        self.assertTrue(bl.direct_reversal(self.ALWAYS, self.PADDED_NEVER))
        self.assertEqual(bl.action_relation(self.ALWAYS, self.PADDED_NEVER),
                         "incompatible")

    def test_a_reversal_counts_however_the_triggers_were_phrased(self):
        """Triggers are free text and the founder says the same situation two
        ways. The trigger floor is a tie breaker, not a veto: it may not turn a
        reversal into 'unrelated'."""
        a = {"scope_type": "global", "scope_key": "",
             "trigger_text": "pushing to github", "action_text": self.ALWAYS}
        b = {"scope_type": "global", "scope_key": "",
             "trigger_text": "publishing commits upstream to the remote host",
             "action_text": self.NEVER}
        v = bl.conflict_verdict(a, b)
        self.assertLess(v["trigger_overlap"], bl.TRIGGER_OVERLAP_FLOOR)
        self.assertEqual(v["verdict"], "contradiction")
        self.assertTrue(v["direct_reversal"])
        self.assertIn("direct reversal", " ".join(v["reasons"]),
                      "a bypassed floor has to say it was bypassed")

    def test_an_intensifier_is_not_a_negation(self):
        """"no exceptions" makes a rule stronger, not opposite. Reading the
        "no" flipped an ordinary REQUIRE to FORBID and manufactured a
        contradiction against a rule that plainly agreed with it, which the
        founder could then only get past by overriding a conflict that did not
        exist."""
        self.assertEqual(bl.polarity("always run the tests, no exceptions"),
                         "require")
        self.assertEqual(bl.polarity("run the migration without fail"), "require")
        self.assertEqual(bl.polarity("livre le rapport sans faute"), "require")
        self.assertEqual(
            bl.action_relation("always run the tests, no exceptions",
                               "always run the tests"),
            "near_duplicate")
        self.assertEqual(bl.polarity("never run the tests"), "forbid",
                         "a real negation still reads as one")

    def test_one_shared_word_does_not_manufacture_a_reversal(self):
        """The containment measure divides by the SHORTER action, so without a
        floor on subject size a single shared token would flag a narrowing as a
        reversal and block an approval that deserves to pass."""
        self.assertFalse(bl.direct_reversal(
            "never commit", "always commit the lock file after every build"))
        self.assertFalse(bl.direct_reversal(
            "always run the tests", "never delete the tests"))

    def test_the_detector_states_what_it_cannot_see(self):
        """"Use tabs" against "use spaces" is a real contradiction and lexical
        comparison cannot find it. Asserted rather than left as a hope: this is
        exactly why `link a contradicts b` exists as a founder command."""
        self.assertEqual(bl.action_relation("indent with tabs",
                                            "indent with four spaces"),
                         "not_comparable")

    def test_scope_relation_never_guesses_containment(self):
        self.assertEqual(bl.scope_relation("global", "", "global", ""), "same")
        self.assertEqual(bl.scope_relation("project", "A", "project", "a"), "same")
        self.assertEqual(bl.scope_relation("project", "A", "project", "B"), "disjoint")
        self.assertEqual(bl.scope_relation("global", "", "project", "A"), "broader")
        self.assertEqual(bl.scope_relation("project", "A", "global", ""), "narrower")
        self.assertEqual(bl.scope_relation("project", "A", "artifact", "x"),
                         "disjoint",
                         "nothing in the store says that artifact lives in that "
                         "project, so claiming containment would be a guess")

    def test_only_the_same_scope_can_reach_a_contradiction(self):
        same = {"scope_type": "global", "scope_key": "",
                "trigger_text": "pushing to GitHub", "action_text": self.ALWAYS}
        other = dict(same, action_text=self.NEVER)
        self.assertEqual(bl.conflict_verdict(same, other)["verdict"], "contradiction")
        narrow = dict(other, scope_type="project", scope_key="SampleApp")
        self.assertEqual(bl.conflict_verdict(same, narrow)["verdict"], "narrowing")
        elsewhere = dict(other, scope_type="project", scope_key="Other")
        far = dict(same, scope_type="project", scope_key="SampleApp")
        self.assertEqual(bl.conflict_verdict(far, elsewhere)["verdict"], "unrelated")

    def test_a_verdict_shows_its_working(self):
        a = {"scope_type": "global", "scope_key": "", "trigger_text": "pushing",
             "action_text": self.ALWAYS}
        v = bl.conflict_verdict(a, dict(a, action_text=self.NEVER))
        self.assertEqual(v["scope_relation"], "same")
        self.assertEqual(v["action_relation"], "incompatible")
        self.assertEqual(len(v["reasons"]), 3)

    def test_supersession_cycles_are_detected_before_and_after_the_fact(self):
        edges = [("a", "b"), ("b", "c")]
        self.assertTrue(bl.supersession_cycle(edges, "c", "a"))
        self.assertFalse(bl.supersession_cycle(edges, "c", "d"))
        self.assertTrue(bl.supersession_cycle([], "a", "a"))
        self.assertEqual(bl.supersession_cycles(edges + [("c", "a")]),
                         ["a", "b", "c"])
        self.assertEqual(bl.supersession_cycles(edges), [])


class TestLoop6LearningVerifyCli(unittest.TestCase):
    """Driven through the real binary, because the exit code IS the deliverable:
    a script has to be able to gate on it."""

    def _run(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def test_verify_exits_zero_when_clean_and_one_when_it_finds_something(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, BROTHERMODE_ROOT=root)
            init = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                                   "init"], cwd=root, env=env,
                                  capture_output=True, text=True)
            self.assertEqual(init.returncode, 0, init.stderr)
            clean = self._run(root, ["verify"])
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertIn("no findings", clean.stdout)

            def capture_and_approve(action, extra=()):
                r = self._run(root, ["capture", "--scope", "global",
                                     "--trigger", "pushing to GitHub",
                                     "--action", action, "--source", "manual"])
                self.assertEqual(r.returncode, 0, r.stderr)
                cid = r.stdout.split()[1]
                # FIX ROUND P3: the conflict guard runs at MINT as well as at
                # approve, and the override flags are in the receipt
                # fingerprint, so both commands are asked the same question. An
                # unresolved contradiction is now refused before the founder is
                # asked at all, which is where that refusal belongs.
                g = self._run(root, ["grant-approval", cid, "--answer",
                                     "the founder answered yes, in a test",
                                     "--json"] + list(extra))
                if g.returncode != 0:
                    return g
                return self._run(root, ["approve", cid, "--ref", "test", "--receipt",
                                        json.loads(g.stdout)["token"]]
                                 + list(extra))

            first = capture_and_approve("always push through the GitHub Desktop app")
            self.assertEqual(first.returncode, 0, first.stderr)
            blocked = capture_and_approve("never push through the GitHub Desktop app")
            self.assertEqual(blocked.returncode, 2,
                             "an unresolved contradiction must not approve")
            self.assertIn("unresolved-contradiction", blocked.stderr)
            forced = capture_and_approve("never push through the GitHub Desktop app",
                                         ["--override-conflict", "both for now"])
            self.assertEqual(forced.returncode, 0, forced.stderr)

            found = self._run(root, ["verify"])
            self.assertEqual(found.returncode, 1, found.stdout)
            self.assertIn("unresolved-contradiction", found.stdout)
            rel = self._run(root, ["relevant", "--query", "pushing to GitHub"])
            self.assertEqual(rel.returncode, 0, rel.stderr)
            self.assertIn("UNRESOLVED CONFLICT", rel.stdout)
            self.assertEqual(rel.stdout.count("rank="), 2,
                             "both sides of a conflict are shown; the tool does not "
                             "pick one")


class TestLoop8ExternalGradingCli(unittest.TestCase):
    """Loop 8 driven through the real binaries. The store tests prove the
    semantics; these prove the founder can actually reach them, which is a
    different claim and the one this project keeps getting wrong."""

    def _learn(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _init(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_founder_can_reach_confirmed_and_grade_a_repeat(self):
        """Found by driving the CLI: before `confirm` existed every rule stayed
        'approved' forever, and the repeat check could never fire on real
        usage even though its semantics were correct."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cap = self._learn(root, ["capture", "--scope", "global",
                                     "--trigger", "writing an executive update",
                                     "--action", "state customer impact first",
                                     "--source", "manual"])
            self.assertEqual(cap.returncode, 0, cap.stderr)
            cid = cap.stdout.split()[1]
            app = self._learn(root, ["approve", cid, "--ref", "founder in chat",
                                     "--receipt", _cli_receipt(self._learn, root, cid)])
            self.assertEqual(app.returncode, 0, app.stderr)
            rid = app.stdout.split()[3]
            rel = self._learn(root, ["relevant", "--query",
                                     "writing an executive update",
                                     "--record-applications", "--session", "S1"])
            self.assertEqual(rel.returncode, 0, rel.stderr)
            apps = self._learn(root, ["applications", "--session", "S1", "--json"])
            aid = json.loads(apps.stdout)[0]["application_uuid"]
            dis = self._learn(root, ["disposition", aid, "followed"])
            self.assertEqual(dis.returncode, 0, dis.stderr)
            conf = self._learn(root, ["confirm", rid, "--because", "held up once"])
            self.assertEqual(conf.returncode, 0, conf.stderr)
            self.assertIn("is now confirmed", conf.stdout)
            again = self._learn(root, ["capture", "--scope", "global",
                                       "--trigger", "writing an executive update",
                                       "--action", "state customer impact first",
                                       "--source", "manual", "--session", "S1"])
            cid2 = again.stdout.split()[1]
            dry = self._learn(root, ["repeat-check", cid2])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("bad_rule", dry.stdout)
            self.assertIn("nothing written", dry.stdout)
            wet = self._learn(root, ["repeat-check", cid2, "--record"])
            self.assertEqual(wet.returncode, 0, wet.stderr)
            self.assertIn("evidence recorded as contradict", wet.stdout)
            fails = self._learn(root, ["loop-failures", "--since", "30d"])
            self.assertEqual(fails.returncode, 0, fails.stderr)
            self.assertIn("repeated settled corrections: 1", fails.stdout)
            outc = self._learn(root, ["rule-outcomes", rid])
            self.assertEqual(outc.returncode, 0, outc.stderr)
            self.assertIn("not a success rate", outc.stdout)

    def test_an_unreadable_window_is_refused_rather_than_defaulted(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            bad = self._learn(root, ["loop-failures", "--since", "last month"])
            self.assertEqual(bad.returncode, 2)
            self.assertIn("cannot read", bad.stderr)

    def test_an_outcome_event_without_a_record_is_refused(self):
        """An outcome that cannot say WHICH work it came from grades nothing,
        so it fails closed rather than landing as an unlinkable row."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            r = self._learn(root, ["rework", "--because", "sent back"])
            self.assertEqual(r.returncode, 2)
            self.assertIn("work record is required", r.stderr)


class TestLoop8TelemetryStaysUnforgeable(unittest.TestCase):
    """The signals Loop 8 grades on are only worth anything if the graded
    session cannot manufacture them. These guard the two ways it could try."""

    def test_a_rating_without_provenance_stays_unattributed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ratings.jsonl")
            old = bm.RATINGS
            bm.RATINGS = path
            try:
                bm.cmd_rate(["--score", "5", "--task", "the exec update"])
                bm.cmd_rate(["--score", "4", "--task", "the exec update",
                             "--reply", "yes that was right", "--session", "S1"])
            finally:
                bm.RATINGS = old
            rows = [json.loads(x) for x in _read(path).splitlines() if x.strip()]
            self.assertEqual([r["attributed"] for r in rows], [False, True])
            self.assertNotIn("reply", rows[0])

    def test_telemetry_cannot_write_founder_approval_evidence(self):
        """Structural, not behavioural: bm_telemetry has no path to the
        learning tables at all, so no session can promote its own candidate by
        emitting a signal. A behavioural test would only prove that today's
        code does not happen to do it."""
        src = _read(os.path.join(HERE, "bm_telemetry.py"))
        for forbidden in ("founder_approval", "approve_learning_candidate",
                          "learning_evidence", "change_learning_rule_state"):
            self.assertNotIn(forbidden, src,
                             "telemetry must not be able to create approval "
                             "evidence: found %r" % forbidden)

    def test_an_outcome_signal_records_its_link_and_flags_when_it_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "signals.jsonl")
            old = bm.SIGNALS
            bm.SIGNALS = path
            try:
                bm.cmd_signal("rework", ["--session", "S1", "--task", "update",
                                         "--record", "abc123",
                                         "--artifact", "notes.md"])
                bm.cmd_signal("rework", ["--session", "S1", "--task", "update"])
            finally:
                bm.SIGNALS = old
            rows = [json.loads(x) for x in _read(path).splitlines() if x.strip()]
            self.assertEqual([r["linked"] for r in rows], [True, False])
            self.assertEqual(rows[0]["record_uuid"], "abc123")
            self.assertEqual(rows[0]["artifact"], "notes.md")


class TestLoop12SecretScrubberBoundary(unittest.TestCase):
    """LOOP 12, CRITICAL. Every vendor pattern in SECRET_PATTERNS was anchored
    with \b, and Python counts "_" as a word character. So a secret with any
    word character in front of it never matched: the token went to sqlite and
    to corrections.jsonl in cleartext while redaction_count recorded 0, which is
    the one signal a reviewer gets that the text was touched at all."""

    PREFIXED = (
        "OPENAI_KEY_sk-live_ABCDEFGH12345678901234",
        "AWSKEY_AKIAIOSFODNN7EXAMPLE",
        "GITHUB_ghp_ABCDEFGHIJKLMNOPQRST",
        "nid_123-45-6789",
        "x_eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0",
        "co_xoxb-1234567890-ABCDEFGHIJ",
    )

    def test_a_word_character_in_front_does_not_hide_a_secret(self):
        for token in self.PREFIXED:
            clean, n = bm.redact(token)
            self.assertGreater(n, 0, "no redaction fired on: %s" % token)
            self.assertIn("[REDACTED]", clean)

    def test_the_unprefixed_form_still_redacts(self):
        # The premise, asserted rather than assumed: if the bare shapes ever
        # stopped matching, the test above would pass for the wrong reason.
        for token in ("sk-live_ABCDEFGH12345678901234", "AKIAIOSFODNN7EXAMPLE",
                      "ghp_ABCDEFGHIJKLMNOPQRST", "123-45-6789"):
            self.assertGreater(bm.redact(token)[1], 0, token)

    def test_ordinary_prose_is_still_left_alone(self):
        # The boundary is "letters and digits bind", not "no boundary at all".
        # A pattern with the anchor simply removed would eat "task-oriented".
        for prose in ("task-oriented-development-plan",
                      "always use the staging bucket, never production",
                      "we should ask-the-platform-team about it"):
            clean, n = bm.redact(prose)
            self.assertEqual(n, 0, "over-redacted ordinary prose: %r" % clean)
            self.assertEqual(clean, prose)


class TestLoop12RedactionIsLinearInInputSize(unittest.TestCase):
    """LOOP 12. The key=value pattern led with an UNBOUNDED [A-Za-z0-9_]*,
    retried at every offset of a long alphanumeric run, so redact() was O(n^2).
    Store.import_correction_inbox redacts the FULL untruncated inbox text and
    the inbox is one file shared by every project on this machine, so one 20 KB
    row stalled the import for 75 seconds of CPU and a 4 MB row never finished.
    A wall-clock budget is a blunt instrument, so this asserts the SHAPE: four
    times the input must not take anything like sixteen times the work."""

    def _time(self, text, samples=5):
        """Minimum of several samples, not one (C-11, 2026-08-04).

        A single wall-clock sample is exposed to one bad scheduling stall
        landing on it alone, and that is exactly what happened: CI run
        30818827958 failed the ratio assertion below on one leg of eleven and
        passed on an unchanged re-run. The comment in that test argued a ratio
        could not be moved by load because both timings run under the same
        load, which holds only when the SAME noise reaches both samples.

        Minimum-of-N is the standard estimator for a microbenchmark because
        noise can only ADD latency to a sample, never remove it, so the
        minimum converges on the true unloaded cost. Five samples means a
        stall would have to land on every draw at one size and none at the
        other to move the ratio. Both call sites in this class share this
        helper, so both are fixed here rather than twice."""
        import time
        best = None
        for _ in range(samples):
            start = time.time()
            bm.redact(text)
            elapsed = time.time() - start
            if best is None or elapsed < best:
                best = elapsed
        return best

    def test_quadratic_blowup_is_gone(self):
        # FIXED 2026-07-31. This test carried BOTH failure modes at once, which
        # is worse than either alone, and its own comment was half true:
        #
        #   assertLess(large, max(small, 0.005) * 40)   CANNOT FAIL for the
        #     reason it exists. A quadratic inflates `small` too, so a ceiling
        #     derived from `small` rises with the defect it is meant to catch.
        #     Demonstrated by reinjecting a quadratic redactor: it produced a
        #     15.6x ratio, textbook O(n^2), and still sat under its own 8.3s
        #     ceiling. The old comment claimed this ceiling was "deliberately
        #     loose so a busy machine cannot fail this", which was true, and
        #     quietly also meant a busy DEFECT could not fail it either.
        #
        #   assertLess(large, 2.0)   CAN FAIL when nothing is wrong. A bare
        #     wall clock measures the machine. Its neighbour with the identical
        #     shape failed at 1023s on a five-session machine while the code
        #     under test was unchanged.
        #
        # Both are replaced by the ratio, which no defect can inflate and no
        # load can move: 4x the input is about 4x the work when linear and
        # about 16x when quadratic, so 8x separates them with room for noise,
        # and both timings are taken under whatever load is present so
        # contention cancels.
        #
        # FIXED 2026-08-04, C-11. "No load can move it" was still only half
        # true: it holds when the SAME noise lands on both samples, and this
        # took exactly ONE sample per size. CI run 30818827958 failed this
        # exact assertion on one leg of eleven and passed on an unchanged
        # re-run, because a single stall on `large` alone was enough while
        # `small` is floored at 0.001 and cannot grow to absorb it. _time()
        # now returns the MINIMUM of five samples per size.
        #
        # CALIBRATED 2026-08-04, and read the sizes here before trusting them
        # to catch anything. This text is a run of LETTERS, and the pattern
        # opens with (?<![A-Za-z0-9]), so only two offsets in the whole string
        # can start a match. This test is a useful regression guard on the
        # overall cost of redact(), but it is NOT the one that proves the
        # quadratic defect is detectable: a reinjected unbounded pattern moves
        # this ratio to 3.9x, still linear. That proof lives in
        # test_calibrated_reinjecting_the_unbounded_pattern_reproduces_the_blowup
        # below, which uses underscores because they are the character that
        # clears the lookbehind at every offset.
        small = max(self._time("x " + "B" * 8000), 0.001)
        large = self._time("x " + "B" * 32000)
        self.assertLess(large / small, 8.0,
                        "redact() scaling looks superlinear: 8000 chars took "
                        "%.4fs, 32000 took %.4fs, a %.1fx ratio where linear is "
                        "about 4x and quadratic about 16x"
                        % (small, large, large / small))

    def test_a_run_of_underscores_does_not_blow_up_either(self):
        # "_" is excluded from the boundary lookbehind on purpose, so it is the
        # one character that can still start a match at every offset. The
        # bounded prefix is what keeps that linear.
        #
        # FIXED 2026-07-31: this test used to be a bare stopwatch,
        # `assertLess(self._time("_" * 32000), 2.0)`, with no baseline. That
        # measures the MACHINE, not the algorithm, and it violated the principle
        # this class's own docstring states two lines above it: a wall-clock
        # budget is a blunt instrument, so assert the SHAPE. Its sibling
        # test_quadratic_blowup_is_gone already did exactly that, and carries a
        # comment saying its ceiling is loose "so a busy machine cannot fail
        # this". This one had no such protection and duly failed on a machine
        # running five sessions: 0.198s unloaded, past 2.0s under load, while
        # the code under test had not changed at all.
        #
        # Shaped, not stopwatched, and the SHAPE IS THE RATIO rather than a
        # multiple of the smaller timing. That distinction was found by
        # calibration, not by reasoning: an `assertLess(large, small * 40)` form
        # was written first and a deliberately reinjected quadratic redactor
        # PASSED it, because a quadratic inflates the small measurement too, so
        # the ceiling it derives from `small` rises with the defect it is meant
        # to catch. Measured: linear gave 0.069s and 0.193s, a 2.8x ratio;
        # quadratic gave 0.208s and 3.243s, a 15.6x ratio, and 3.243 still sat
        # under its 8.3s ceiling.
        #
        # 4x the input is 4x the work when linear and 16x when quadratic, so the
        # ratio separates them cleanly and a ceiling on it cannot be inflated by
        # the defect. 8x leaves generous room for noise while staying half the
        # quadratic signature. Both timings are taken under whatever load is
        # present, so contention scales them together and cancels in the ratio,
        # which is the property the old absolute ceiling did not have.
        #
        # FIXED 2026-08-04, C-11. This test shares _time() with
        # test_quadratic_blowup_is_gone above and carried the identical
        # single-sample exposure, so the minimum-of-five change there fixes
        # this one too with no edit needed here.
        #
        # CALIBRATED 2026-08-04, and THIS is the test that carries the
        # detection property. Underscores clear the boundary lookbehind at
        # every offset, so the unbounded pattern gets n starting positions and
        # goes quadratic on exactly this input shape. That is proven by
        # test_calibrated_reinjecting_the_unbounded_pattern_reproduces_the_blowup
        # below, which reinjects the pre-Loop-12 pattern and measures 15.8x
        # against the 4.1x this bounded pattern gives. The 15.6x recorded
        # three paragraphs up was measured on underscores as well, which is
        # why the two figures agree.
        small = max(self._time("_" * 8000), 0.001)
        large = self._time("_" * 32000)
        self.assertLess(large / small, 8.0,
                        "redact() on an underscore run looks superlinear: 8000 "
                        "chars took %.4fs, 32000 took %.4fs, a %.1fx ratio "
                        "where linear is about 4x and quadratic about 16x"
                        % (small, large, large / small))

    def test_calibrated_reinjecting_the_unbounded_pattern_reproduces_the_blowup(self):
        # CALIBRATION for the two tests above: C-11's adversarial half, and the
        # correction of an earlier reading recorded here on 2026-08-04.
        #
        # THE EARLIER READING, and why it was wrong. A calibration test was
        # written and then removed because reinjecting the pre-Loop-12
        # unbounded key-value pattern produced a 4.0x ratio, which is linear.
        # The note left behind concluded that the pattern "is not quadratic on
        # a run of one repeated character". That conclusion was too broad: it
        # generalised from ONE repeated character to all of them. The probe had
        # used the "x " + "B" * n text from the sibling test above, and a run of
        # LETTERS cannot exercise this pattern at all. The pattern opens with
        # the boundary lookbehind (?<![A-Za-z0-9]), so inside a run of "B" every
        # offset but the first is preceded by an alphanumeric and is rejected
        # before any backtracking happens. Two start positions do work in the
        # whole string, so the cost is linear no matter what follows.
        #
        # "_" is the one character that is EXCLUDED from that lookbehind while
        # still being INSIDE the [A-Za-z0-9_]* run that follows it, which is
        # exactly what the sibling test's own first sentence says. So every one
        # of n offsets clears the lookbehind, and each one then scans the rest
        # of the run looking for the keyword alternation that never arrives:
        # n starts times n characters, the O(n^2) this class exists to catch.
        # The bound {0,40} on the shipped pattern is what caps the inner scan
        # at a constant and turns the same shape linear.
        #
        # Measured on this machine, minimum of five samples per size, 1000 and
        # 4000 underscores: unbounded gave 0.0263s and 0.4160s, a 15.8x ratio;
        # the shipped bounded pattern gave 0.0023s and 0.0093s, a 4.1x ratio.
        # That 15.8x is the same signature as the 15.6x the sibling comment
        # above records, which is corroboration rather than coincidence: that
        # figure was measured on underscores too. Sizes are 1000 and 4000
        # rather than 8000 and 32000 to keep this test near 2s instead of near
        # 130s; quadratic cost rises fast and the ratio is what is being
        # asserted, not the absolute time.
        #
        # If this test ever stops reproducing, the two tests above are no
        # longer calibrated to the defect they exist to catch, and the
        # minimum-of-5 estimator in _time() may be suppressing a real
        # regression rather than only suppressing noise.
        unbounded = re.compile(
            bm._BEFORE + r"[A-Za-z0-9_]*(?:pass(?:word|wd|phrase)?"
            r"|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
            r"|credential)s?\s*(?:[:=]|\s+(?:is|was)\s+)\s*\S+", re.I)
        original = bm.SECRET_PATTERNS
        reinjected = list(original)
        reinjected[8] = unbounded
        bm.SECRET_PATTERNS = reinjected
        try:
            small = max(self._time("_" * 1000), 0.001)
            large = self._time("_" * 4000)
        finally:
            bm.SECRET_PATTERNS = original
        self.assertGreaterEqual(
            large / small, 8.0,
            "REINJECTION CHECK: the pre-Loop-12 unbounded key=value pattern "
            "must still show a superlinear ratio, so that the two tests above "
            "are proven able to catch it. 1000 chars took %.4fs, 4000 took "
            "%.4fs, a %.1fx ratio where 15.8x was measured when this was "
            "written and anything near 4x means the calibration has been lost"
            % (small, large, large / small))

    def test_calibration_reinjection_is_reverted_afterwards(self):
        # The calibration above swaps a real product symbol and restores it in
        # a finally block. If that restore ever broke, every later redaction
        # test in this process would run against the reinjected pattern and
        # could pass or fail for reasons that have nothing to do with the code
        # under test. This asserts the shipped pattern is the bounded one, so
        # a leaked monkeypatch fails here by name instead of surfacing as an
        # unrelated flake somewhere downstream.
        self.assertIn("{0,40}", bm.SECRET_PATTERNS[8].pattern)


class TestLoop12PairedArtifactsAreRedacted(unittest.TestCase):
    """LOOP 12. The Loop 4 transcript-pairing field wrote tool_use file paths
    verbatim into corrections.jsonl while both neighbouring fields (text and
    prev_response_excerpt) went through redact(). Paths carry secrets routinely:
    a token-named key file, a per-tenant directory, a temp dir built from a
    credential."""

    SECRET_PATH = "/home/f/sk-live-ABCDEFGHIJKLMNOP12345/AKIA1234567890ABCDEF/notes.txt"

    def _run(self, vault):
        repo = tempfile.mkdtemp()
        tp = os.path.join(repo, "transcript.jsonl")
        lines = []
        for i in range(6):
            lines.append({"type": "assistant",
                          "timestamp": "2026-07-29T10:1%d:00Z" % i,
                          "message": {"id": "m%d" % i, "model": "opus",
                                      "usage": {"output_tokens": 10, "input_tokens": 10},
                                      "content": [
                                          {"type": "text", "text": "ok"},
                                          {"type": "tool_use", "name": "Read",
                                           "input": {"file_path": self.SECRET_PATH}}]}})
        for i in range(6):
            lines.append({"type": "user", "timestamp": "2026-07-29T10:2%d:00Z" % i,
                          "message": {"content":
                                      "no, that is wrong, do it the other way %d" % i}})
        with io.open(tp, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(o) for o in lines) + "\n")
        payload = json.dumps({"transcript_path": tp, "session_id": "sess-art",
                              "cwd": repo, "reason": "other"})
        subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),  # sbe: allow-silent outcomes-append fixture call whose effect is verified by reading corrections.jsonl afterward
                        "outcomes-append"],
                       input=payload, env=_consented_env(repo, vault),
                       cwd=repo, capture_output=True, text=True)
        return os.path.join(vault, "99-System", "telemetry", "corrections.jsonl")

    def test_a_secret_shaped_path_never_reaches_the_corrections_file(self):
        with tempfile.TemporaryDirectory() as v:
            path = self._run(v)
            self.assertTrue(os.path.exists(path), "nothing was captured at all")
            body = _read(path)
            self.assertNotIn("sk-live-ABCDEFGHIJKLMNOP12345", body,
                             "an api-key-shaped path segment was written verbatim")
            self.assertNotIn("AKIA1234567890ABCDEF", body,
                             "an aws-key-shaped path segment was written verbatim")
            rec = json.loads(body.splitlines()[0])
            self.assertTrue(rec.get("artifacts"), "the pairing field disappeared")
            self.assertEqual(rec.get("artifact_redactions"), 2,
                             "the count must show the artifacts were touched")
            # TIGHTENED 2026-08-16 (SBE17). This test used to assert that the
            # harmless tail, notes.txt, SURVIVED beside the [REDACTED] secret
            # segments, on the reasoning that the pairing field would be
            # useless without it. That encoded the weaker privacy model a
            # security review then named: whenever no secret-shaped substring
            # happened to be present, the whole absolute path went to the
            # ledger verbatim, revealing directory and client structure. The
            # fix pairs mask_absolute_paths with redact(), exactly as
            # bs.export_column already did, and masking replaces the WHOLE
            # matched path including its tail. So the assertion inverts: what
            # must be true now is that no part of the absolute path reaches
            # the file, and the pairing is judged by the redaction count and
            # the withheld marker instead.
            self.assertNotIn("notes.txt", rec["artifacts"][0],
                             "the path tail must not survive masking")
            self.assertTrue(
                "[PATH WITHHELD]" in rec["artifacts"][0]
                or "[REDACTED]" in rec["artifacts"][0],
                "the artifact must still be visibly accounted for")


class TestLoop12LearningCliPrivacy(unittest.TestCase):
    """LOOP 12, driven through the real binaries because the leak was in what
    the COMMANDS print, not in what the store holds."""

    def _learn(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _init(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_field_the_founder_types_is_scrubbed_not_only_raw_text(self):
        """proposed_trigger, proposed_action, proposed_because and
        proposed_domain went through normalize_text alone, so a secret typed
        into --action was on disk in cleartext and printed by `candidates`,
        `rules` and `why`, while raw_text next to it was masked."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            r = self._learn(root, [
                "capture", "--scope", "global",
                "--raw", "RAW sk-live_ABCDEFGH12345678901234",
                "--trigger", "TRIG sk-live_ABCDEFGH12345678901234",
                "--action", "ACT AKIAIOSFODNN7EXAMPLE",
                "--because", "BEC ghp_ABCDEFGHIJKLMNOPQRST"])
            self.assertEqual(r.returncode, 0, r.stderr)
            cid = r.stdout.split()[1]
            store = bs.Store(root)
            try:
                cand = store.get_learning_candidate(cid)
            finally:
                store.close()
            for col in ("raw_text", "proposed_trigger", "proposed_action",
                        "proposed_because"):
                self.assertIn("[REDACTED]", cand[col],
                              "%s was stored unscrubbed: %r" % (col, cand[col]))
            for secret in ("sk-live_ABCDEFGH12345678901234",
                           "AKIAIOSFODNN7EXAMPLE", "ghp_ABCDEFGHIJKLMNOPQRST"):
                self.assertNotIn(secret, json.dumps(dict(cand)))
            self.assertEqual(cand["redaction_count"], 4,
                             "the count must cover every scrubbed field, not "
                             "only raw_text")
            # And it stays scrubbed through approval into the rule version.
            a = self._learn(root, ["approve", cid, "--ref", "test", "--receipt",
                                   _cli_receipt(self._learn, root, cid)])
            self.assertEqual(a.returncode, 0, a.stderr)
            rules = self._learn(root, ["rules"])
            self.assertEqual(rules.returncode, 0, rules.stderr)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", rules.stdout)
            self.assertIn("[REDACTED]", rules.stdout)

    def test_no_json_command_pipes_the_founders_verbatim_words(self):
        """dump withholds raw_text and evidence.excerpt entirely and
        show-candidate --json withholds raw_text behind a flag. `candidates
        --json` and `why --json` printed the same columns in full, with no flag
        and no warning, so the property was enforced in three places and
        bypassed in two."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            quote = "the client in Lyon keeps changing his mind about the price"
            r = self._learn(root, ["capture", "--scope", "global", "--raw", quote,
                                   "--trigger", "pushing to GitHub",
                                   "--action", "use GitHub Desktop"])
            self.assertEqual(r.returncode, 0, r.stderr)
            cid = r.stdout.split()[1]
            listed = self._learn(root, ["candidates", "--json"])
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertNotIn(quote, listed.stdout,
                             "candidates --json piped the verbatim quote")
            self.assertIn("[withheld: pass --show-source]", listed.stdout)
            shown = self._learn(root, ["candidates", "--json", "--show-source"])
            self.assertIn(quote, shown.stdout,
                          "--show-source must still return it, or the flag is a lie")

            a = self._learn(root, ["approve", cid, "--ref", "test", "--receipt",
                                   _cli_receipt(self._learn, root, cid)])
            self.assertEqual(a.returncode, 0, a.stderr)
            rules = json.loads(self._learn(root, ["rules", "--json"]).stdout)
            rid = rules[0]["rule_uuid"][:8]
            why = self._learn(root, ["why", rid, "--json"])
            self.assertEqual(why.returncode, 0, why.stderr)
            self.assertNotIn(quote, why.stdout,
                             "why --json piped the evidence excerpt in full")
            self.assertIn("[withheld: pass --show-source]", why.stdout)
            why_open = self._learn(root, ["why", rid, "--json", "--show-source"])
            self.assertIn(quote, why_open.stdout)

    def test_a_control_character_in_a_scope_key_is_refused(self):
        """safe_display is the containment for control characters in CLI and
        injected output, and scope_key was the one displayed field that never
        met it. An ESC in a scope key cleared the founder's screen and repainted
        the neighbouring rule lines in the injected RELEVANT FOUNDER RULES
        block."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            bad = "p\x1b[2J\x1b[31mPWNED"
            r = self._learn(root, ["capture", "--trigger", "python style",
                                   "--action", "use tabs", "--scope", "project",
                                   "--scope-key", bad])
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("bad-scope", r.stderr)
            self.assertIn("control character", r.stderr)

    def test_an_escape_already_in_the_store_still_cannot_reach_the_terminal(self):
        """Validation only protects NEW rows. Every existing store keeps
        printing, so the display path has to hold on its own."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            r = self._learn(root, ["capture", "--trigger", "python style",
                                   "--action", "use tabs", "--scope", "project",
                                   "--scope-key", "plain"])
            self.assertEqual(r.returncode, 0, r.stderr)
            cid = r.stdout.split()[1]
            self.assertEqual(self._learn(root, ["approve", cid, "--ref", "t", "--receipt",
                                                _cli_receipt(self._learn, root, cid)]).returncode, 0)
            store = bs.Store(root)
            try:
                store.conn.execute("UPDATE learning_rules SET scope_key = ?",
                                   ("p\x1b[2J\x1b[31mPWNED",))
                store.conn.commit()
            finally:
                store.close()
            for args in (["rules"], ["relevant", "--query", "python style tabs",
                                     "--project", "p\x1b[2J\x1b[31mPWNED"]):
                out = self._learn(root, args)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertNotIn("\x1b", out.stdout,
                                 "a raw ESC reached the terminal from %s" % args[0])
                self.assertIn("PWNED", out.stdout,
                              "the key itself must still be readable, inert")

    def test_one_malformed_timestamp_does_not_hide_every_later_correction(self):
        """inbox promises malformed rows are counted and named, never a
        traceback. A non-string ts crashed the display loop, so a bad row placed
        FIRST silently suppressed every genuine correction after it while
        --backfill still imported them."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            path = os.path.join(root, "bad.jsonl")
            rows = [{"ts": 1, "project": "p", "session_id": "s0",
                     "text": "always use spaces"},
                    {"ts": None, "project": "p", "session_id": "s1",
                     "text": "stop reformatting my files"},
                    {"ts": "2026-07-29T00:00:00Z", "project": "p",
                     "session_id": "s2", "text": "you never run the tests"}]
            with io.open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            out = self._learn(root, ["inbox", "--file", path])
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("Traceback", out.stderr)
            for text in ("always use spaces", "stop reformatting my files",
                         "you never run the tests"):
                self.assertIn(text, out.stdout,
                              "a later correction was suppressed by an earlier "
                              "malformed row")
            self.assertIn("2026-07-29T00:00:00", out.stdout)


class TestPostAuditLoop3ApprovalReceiptCli(unittest.TestCase):
    """Post-audit LOOP 3 at the CLI boundary, which is where the audit finding
    actually lived.

    The repro, run against HEAD d88abcc in a throwaway store on 2026-07-29:
    `bm_learn.py approve <id> --gate`, with no --ref and no receipt, exited 0
    and printed "approved as rule 61de7eb9". The help text three lines from the
    top of the same file said approval refuses without a recorded reference.
    The CLI was synthesizing that reference itself, so the sentence was true
    about the code and false about the world."""

    def _learn(self, root, args, env_extra=None):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _init(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _candidate(self, root):
        r = self._learn(root, ["capture", "--trigger", "pushing to github",
                               "--action", "use the desktop app",
                               "--source", "manual"])
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()[1]

    def test_the_exact_audit_repro_now_refuses_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            r = self._learn(root, ["approve", cid, "--gate"])
            self.assertEqual(r.returncode, 2,
                             "the audit repro still succeeds: %s" % r.stdout)
            self.assertIn("no-approval-receipt", r.stderr)
            self.assertNotIn("approved as rule", r.stdout)
            rules = self._learn(root, ["rules", "--json"])
            self.assertEqual(json.loads(rules.stdout or "[]"), [])

    def test_a_supplied_ref_alone_is_not_enough(self):
        """--ref was the old gate. It is now the readable half of provenance,
        and a test that only checked the no-ref case would let it quietly
        become the gate again."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            r = self._learn(root, ["approve", cid, "--ref", "the founder said yes"])
            self.assertEqual(r.returncode, 2)
            self.assertIn("no-approval-receipt", r.stderr)

    def test_the_help_text_promises_only_what_the_code_enforces(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            h = self._learn(root, ["--help"])
            self.assertEqual(h.returncode, 0, h.stderr)
            self.assertIn("grant-approval", h.stdout,
                          "the help must name the command that mints a receipt")
            for claim in ("proves which human", "verifies your identity",
                          "authenticates the founder"):
                self.assertNotIn(claim, h.stdout.lower(),
                                 "the help claims an identity guarantee that "
                                 "does not exist")

    def test_grant_then_approve_is_the_only_way_through(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer",
                                   "yes, always the desktop app", "--json"])
            self.assertEqual(g.returncode, 0, g.stderr)
            token = json.loads(g.stdout)["token"]
            a = self._learn(root, ["approve", cid, "--receipt", token,
                                   "--ref", "the founder said yes in chat"])
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertIn("approved as rule", a.stdout)

    def test_the_token_arrives_by_environment_so_it_stays_out_of_ps_and_history(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer", "yes",
                                   "--json"])
            token = json.loads(g.stdout)["token"]
            a = self._learn(root, ["approve", cid, "--ref",
                                   "the founder said yes in chat"],
                            env_extra={"BM_APPROVAL_RECEIPT": token})
            self.assertEqual(a.returncode, 0, a.stderr)

    def test_a_replayed_token_refuses_at_the_cli(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer", "yes",
                                   "--json"])
            token = json.loads(g.stdout)["token"]
            self.assertEqual(
                self._learn(root, ["approve", cid, "--receipt", token,
                                   "--ref", "the founder said yes"]).returncode, 0)
            second = self._learn(root, ["approve", cid, "--receipt", token,
                                        "--ref", "the founder said yes"])
            self.assertEqual(second.returncode, 2)
            rules = json.loads(self._learn(root, ["rules", "--json"]).stdout)
            self.assertEqual(len(rules), 1, "a replay created a second rule")

    def test_shaping_the_rule_differently_than_the_founder_saw_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer",
                                   "yes, as a soft preference", "--json"])
            token = json.loads(g.stdout)["token"]
            r = self._learn(root, ["approve", cid, "--receipt", token, "--gate",
                                   "--ref", "the founder said yes"])
            self.assertEqual(r.returncode, 2,
                             "a soft answer was silently upgraded to a gate")
            self.assertIn("receipt-stale-candidate", r.stderr)

    def test_the_token_is_printed_once_and_never_by_any_other_command(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer", "yes"])
            self.assertEqual(g.returncode, 0, g.stderr)
            token = [w for w in g.stdout.split() if len(w) == 48][0]
            a = self._learn(root, ["approve", cid, "--receipt", token,
                                   "--ref", "the founder said yes"])
            self.assertEqual(a.returncode, 0, a.stderr)
            for cmd in (["candidates"], ["rules"], ["rules", "--json"],
                        ["why", json.loads(
                            self._learn(root, ["rules", "--json"]).stdout
                        )[0]["rule_uuid"][:8]]):
                out = self._learn(root, cmd)
                self.assertNotIn(token, out.stdout + out.stderr,
                                 "%r printed the receipt token" % (cmd,))
            dump = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_store.py"), "dump"],
                cwd=root, env=dict(os.environ, BROTHERMODE_ROOT=root),
                capture_output=True, text=True)
            self.assertNotIn(token, dump.stdout)


class TestLoopP4bZeroResultDisclosure(unittest.TestCase):
    """LOOP P4 follow-up, driven through the real binary because the defect was
    in what the COMMAND printed, not in what retrieval returned.

    Loop P4 added a two-sentence footer separating gate delivery from soft
    omission, and put it at the BOTTOM of `relevant`. The zero-result branch
    returns before that point. So with one soft rule that matched and a limit
    that cut it (--limit 0, --limit -1), the founder read "none matched" and no
    omission count at all, while `--json` on the identical call reported
    eligible=1, omitted=1, soft_omitted=1. Reproduced on a throwaway store at
    c78e5ea before this changed."""

    def _learn(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _init(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    QUERY = "deploying the website to production"

    def _one_soft_global_rule(self, root):
        """The exact fixture from the reproduction: ONE live soft global rule
        whose trigger the query matches, so any empty result is the limit's
        doing and nothing else."""
        self._init(root)
        c = self._learn(root, ["capture", "--scope", "global", "--scope-key", "",
                               "--trigger", self.QUERY,
                               "--action", "run the smoke tests first"])
        self.assertEqual(c.returncode, 0, c.stderr)
        cid = c.stdout.split()[1]
        a = self._learn(root, ["approve", cid, "--ref", "test", "--receipt",
                               _cli_receipt(self._learn, root, cid)])
        self.assertEqual(a.returncode, 0, a.stderr)

    def test_a_limit_that_cuts_every_soft_rule_says_so_instead_of_none_matched(self):
        for limit in ("0", "-1"):
            with tempfile.TemporaryDirectory() as root:
                self._one_soft_global_rule(root)
                r = self._learn(root, ["relevant", "--query", self.QUERY,
                                       "--limit", limit])
                self.assertEqual(r.returncode, 0, r.stderr)
                # The JSON from the identical call is the ground truth the
                # human output has to agree with.
                j = self._learn(root, ["relevant", "--query", self.QUERY,
                                       "--limit", limit, "--json"])
                self.assertEqual(j.returncode, 0, j.stderr)
                self.assertEqual(json.loads(j.stdout)["soft_omitted"], 1)
                self.assertIn("1 omitted by --limit %s" % limit, r.stdout,
                              "--limit %s hid a rule and the screen never said "
                              "so: %r" % (limit, r.stdout))
                self.assertNotIn("none matched", r.stdout,
                                 "--limit %s reported a rule that DID match as "
                                 "not matching: %r" % (limit, r.stdout))
                # The gate half of the guarantee is stated on this path too,
                # and stated truthfully: nothing was applicable, nothing hidden.
                self.assertIn("Gates: 0 of 0 applicable returned", r.stdout)

    def test_a_genuinely_empty_result_still_says_none_matched(self):
        """The other direction, so the fix cannot pass by shouting "omitted" at
        every empty result. Nothing in scope, nothing omitted, and the founder
        is told exactly that."""
        with tempfile.TemporaryDirectory() as root:
            self._one_soft_global_rule(root)
            r = self._learn(root, ["relevant", "--query",
                                   "zebra xylophone quarantine", "--limit", "5"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("none matched", r.stdout)
            self.assertIn("Soft rules: 0 shown, none omitted.", r.stdout)
            self.assertNotIn("omitted by --limit", r.stdout)

    def test_the_footer_has_exactly_one_definition_in_the_source(self):
        """STRUCTURAL. The defect was two output paths that had to agree about
        omissions and only one of which said anything. A second inline copy of
        either sentence is that same divergence coming back, so the sentences
        live in _delivery_footer and appear once."""
        with io.open(os.path.join(HERE, "bm_learn.py"), encoding="utf-8") as f:
            src = f.read()
        for sentence in ("Gates: %d of %d applicable returned",
                         "omitted by --limit %d"):
            self.assertEqual(src.count(sentence), 1,
                             "%r appears %d times; it must have one definition"
                             % (sentence, src.count(sentence)))

    def test_calibrated_reinjecting_the_silent_zero_result_path_reproduces_it(self):
        """CALIBRATION. Replace the real product symbol
        bm_learn._delivery_footer with the pre-fix behaviour (the gate sentence
        alone, no soft omission count), call cmd_relevant IN-PROCESS so the
        patch is actually seen, and confirm the assertion above fails. A green
        suite without this means nothing."""
        with tempfile.TemporaryDirectory() as root:
            self._one_soft_global_rule(root)
            original = learn_mod._delivery_footer
            learn_mod._delivery_footer = lambda res, limit: learn_mod._out(
                "0 of your %d applicable gate rules were held back; a result "
                "limit cannot hide one." % res.get("gates_total", 0))
            buf = io.StringIO()
            old_root = os.environ.get("BROTHERMODE_ROOT")
            os.environ["BROTHERMODE_ROOT"] = root
            try:
                with contextlib.redirect_stdout(buf):
                    code = learn_mod.cmd_relevant(["--query", self.QUERY,
                                                   "--limit", "0"])
            finally:
                learn_mod._delivery_footer = original
                if old_root is None:
                    del os.environ["BROTHERMODE_ROOT"]
                else:
                    os.environ["BROTHERMODE_ROOT"] = old_root
            self.assertEqual(code, 0)
            self.assertNotIn(
                "omitted by --limit", buf.getvalue(),
                "REINJECTION CHECK: with the footer reverted to its pre-fix "
                "body, --limit 0 must go back to hiding the omission; if this "
                "fails, the tests above are not calibrated to the defect they "
                "claim to catch: %r" % buf.getvalue())


class LookupApplySplitTest(unittest.TestCase):
    """LOOP P5. Substantial work used to depend on the model remembering
    --record-applications on `relevant`. Forgetting it produced a run that
    looked identical and left no application row at all, so "was the rule
    followed" was unanswerable and nothing anywhere said so. Reproduced at
    bc25e06: `relevant --query ...` returned 0, printed the rules, and
    learning_applications stayed empty.

    The fix is a verb split, not a better default: `lookup` never writes,
    `apply` always records and refuses without --session."""

    QUERY = "pushing this branch to github"

    def _learn(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _rule(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        c = self._learn(root, ["capture", "--scope", "global", "--scope-key", "",
                               "--trigger", self.QUERY,
                               "--action", "use the GitHub Desktop app"])
        self.assertEqual(c.returncode, 0, c.stderr)
        cid = c.stdout.split()[1]
        a = self._learn(root, ["approve", cid, "--ref", "test", "--receipt",
                               _cli_receipt(self._learn, root, cid)])
        self.assertEqual(a.returncode, 0, a.stderr)

    def _rows(self, root):
        db = os.path.join(root, ".brothermode", "store.sqlite3")
        con = sqlite3.connect(db)
        try:
            return con.execute(
                "SELECT COUNT(*) FROM learning_applications").fetchone()[0]
        finally:
            con.close()

    def test_lookup_changes_no_rows_and_refuses_the_writing_flags(self):
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            before = self._rows(root)
            r = self._learn(root, ["lookup", "--query", self.QUERY])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("use the GitHub Desktop app", r.stdout)
            self.assertEqual(self._rows(root), before,
                             "lookup wrote an application row")
            for flag in ("--session", "--record"):
                b = self._learn(root, ["lookup", "--query", self.QUERY, flag,
                                       "x"])
                self.assertEqual(b.returncode, 2, b.stdout)
                self.assertIn("lookup never writes", b.stderr)
                self.assertIn("apply", b.stderr)

    def test_apply_requires_a_session_and_records_every_returned_rule(self):
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            n = self._learn(root, ["apply", "--query", self.QUERY])
            self.assertEqual(n.returncode, 2, n.stdout)
            self.assertIn("requires --session", n.stderr)
            self.assertEqual(self._rows(root), 0)
            # LOOP 4: --session alone is no longer a work identity. The bare
            # call refuses at exit 2 (a USAGE refusal, before retrieval, which
            # is why it prints no rules) and names all three ways forward.
            i = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1"])
            self.assertEqual(i.returncode, 2, i.stdout)
            self.assertIn("requires a work identity", i.stderr)
            self.assertIn("--new-record", i.stderr)
            self.assertEqual(self._rows(root), 0,
                             "a refused apply must record nothing")
            r = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1", "--new-record", "p5-rules"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("use the GitHub Desktop app", r.stdout)
            self.assertIn("recorded 1 application(s)", r.stdout)
            self.assertIn("status: recorded.", r.stdout)
            self.assertEqual(self._rows(root), 1)

    def test_re_running_apply_is_idempotent(self):
        """LOOP 4: idempotency is now per work record, so the re-runs have to
        name the SAME record. Repeating --new-record would be three DIFFERENT
        tasks that happen to share wording, which is exactly the case Loop 4
        exists to keep apart, so it must NOT collapse to one row."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            first = self._learn(root, ["apply", "--query", self.QUERY,
                                       "--session", "s1", "--new-record",
                                       "p5-idem"])
            self.assertEqual(first.returncode, 0, first.stderr)
            rec = re.search(r"provisional work record ([0-9a-f]{8})",
                            first.stdout)
            self.assertIsNotNone(rec, first.stdout)
            for _ in range(2):
                r = self._learn(root, ["apply", "--query", self.QUERY,
                                       "--session", "s1", "--record",
                                       rec.group(1)])
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self._rows(root), 1,
                             "apply duplicated rows on re-run")
            self.assertIn("1 already recorded", r.stdout)

    def test_apply_against_a_record_claimed_first_lands_the_link_immediately(self):
        """LOOP 4 REPLACED THIS TEST'S PREMISE, and the old premise is recorded
        here rather than deleted, because a reader deserves to know the order
        changed on purpose.

        It used to assert the order "retrieve bare, THEN claim, then link the
        earlier row afterwards". That order is gone: a bare apply with session
        identity alone now refuses, so there is no unlinked earlier row left to
        rescue. The natural order the execution plan asks for is the reverse,
        record first and application second, and it is better because the link
        is never absent even briefly.

        What survives unchanged is the property the old test actually protected:
        the work record reaches the application row, since nothing else in this
        codebase can set that column afterwards."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            env = dict(os.environ, BROTHERMODE_ROOT=root)
            c = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                                "claim", "p5-late-record"], cwd=root, env=env,
                               capture_output=True, text=True)
            self.assertEqual(c.returncode, 0, c.stderr)
            uuid_m = re.search(r"lifecycle ([0-9a-f]{32})", c.stdout)
            self.assertIsNotNone(uuid_m, c.stdout)
            applied = self._learn(root, ["apply", "--query", self.QUERY,
                                         "--session", "s1", "--record",
                                         uuid_m.group(1)])
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(self._rows(root), 1)
            db = os.path.join(root, ".brothermode", "store.sqlite3")
            con = sqlite3.connect(db)
            try:
                rec = con.execute("SELECT record_uuid FROM "
                                  "learning_applications").fetchone()[0]
            finally:
                con.close()
            self.assertEqual(rec, uuid_m.group(1),
                             "the work record never landed on the row")

    def test_a_failed_recording_returns_the_rules_and_a_loud_partial_status(self):
        """The rules must survive a bookkeeping failure, and the failure must
        be impossible to read as success: status line, and exit 3."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            store_mod = learn_mod.bs
            original = store_mod.Store.record_learning_applications

            def broken(self, query, **kw):
                res = self.retrieve_learning_rules(
                    query, context=kw.get("context"), limit=kw.get("limit", 5))
                out = dict(res)
                out.update({"task_fingerprint": "deadbeef", "recorded": 0,
                            "already_recorded": 0, "linked": 0,
                            "applications": [],
                            "record_error": "disk is on fire"})
                return out

            store_mod.Store.record_learning_applications = broken
            buf = io.StringIO()
            old_root = os.environ.get("BROTHERMODE_ROOT")
            os.environ["BROTHERMODE_ROOT"] = root
            try:
                with contextlib.redirect_stdout(buf):
                    # LOOP 4: a work identity is required, so this call has to
                    # supply one to reach the recording path at all. That is
                    # the point of the test: this is the path that REACHED
                    # retrieval and then failed to record, which still exits 3
                    # with the rules printed. The identity refusal is exit 2
                    # and lives before retrieval, a different thing entirely.
                    code = learn_mod.cmd_apply(["--query", self.QUERY,
                                                "--session", "s1",
                                                "--new-record", "p5-broken"])
            finally:
                store_mod.Store.record_learning_applications = original
                if old_root is None:
                    del os.environ["BROTHERMODE_ROOT"]
                else:
                    os.environ["BROTHERMODE_ROOT"] = old_root
            out = buf.getvalue()
            self.assertEqual(code, 3, "a failed recording exited %d" % code)
            self.assertIn("use the GitHub Desktop app", out,
                          "the rules were lost to a bookkeeping failure")
            self.assertIn("STATUS: PARTIAL. RULES RETRIEVED, APPLICATION NOT "
                          "RECORDED.", out)
            self.assertIn("disk is on fire", out)
            self.assertNotIn("status: recorded.", out)

    def test_a_bad_record_argument_still_prints_the_rules_and_exits_partial(self):
        """A BOOKKEEPING ARGUMENT MUST NOT SWALLOW THE FOUNDER RULES.

        Resolving --record used to happen before retrieval and outside the
        block that turns write failures into a partial status, so a stale or
        mistyped work id aborted the whole run: exit 2, EMPTY stdout, not one
        rule shown. references/learned-rules.md ships the sentence (moved
        from SKILL.md, R2) "never read a nonzero exit as 'no rules'", so an
        agent following the law it was given would read that
        empty run as a partial success and do substantial work with zero
        founder rules surfaced. The rules print, the status is loud, and the
        remedy converges: re-running the identical command cannot fix a bad
        argument, so the text must not tell anyone to."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            r = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1", "--record", "deadbeef"])
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("use the GitHub Desktop app", r.stdout,
                          "a bad --record hid the founder rules")
            self.assertIn("STATUS: PARTIAL. RULES RETRIEVED, APPLICATION NOT "
                          "RECORDED.", r.stdout)
            self.assertIn("no record matches 'deadbeef'", r.stdout)
            self.assertIn("Re-running the identical command will fail "
                          "identically.", r.stdout)
            self.assertNotIn("re-run the identical apply", r.stdout,
                             "prescribed a remedy that never converges")
            self.assertEqual(self._rows(root), 0,
                             "a refused --record still wrote a row")
            j = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1", "--record", "deadbeef", "--json"])
            self.assertEqual(j.returncode, 3, j.stderr)
            payload = json.loads(j.stdout)
            self.assertEqual(payload["recording_status"],
                             "partial-recording-failed")
            self.assertEqual(payload["record_error_kind"], "not-found")
            self.assertTrue(payload["results"], "the JSON twin lost the rules")

    def test_a_second_unit_of_work_in_one_session_gets_its_own_row(self):
        """TWO PIECES OF WORK ARE TWO APPLICATIONS OF THE RULE.

        The idempotence key was (task fingerprint, rule, version, session), and
        the fingerprint is derived from the query alone. Two different pieces of
        substantial work in one session worded the same way therefore collapsed
        onto ONE row: the second recorded nothing while printing
        "status: recorded." and exiting 0, so "was this rule followed for that
        work" was unanswerable and the run read as a clean success. That is the
        exact hole this loop exists to close, moved off a forgotten flag and
        onto a shared sentence."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            env = dict(os.environ, BROTHERMODE_ROOT=root)
            records = []
            for name in ("p5-work-a", "p5-work-b"):
                c = subprocess.run(
                    [sys.executable, os.path.join(HERE, "bm_store.py"),
                     "claim", name], cwd=root, env=env, capture_output=True,
                    text=True)
                self.assertEqual(c.returncode, 0, c.stderr)
                m = re.search(r"lifecycle ([0-9a-f]{32})", c.stdout)
                self.assertIsNotNone(m, c.stdout)
                records.append(m.group(1))
            a = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1", "--record", records[0]])
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertIn("recorded 1 application(s)", a.stdout)
            b = self._learn(root, ["apply", "--query", self.QUERY, "--session",
                                   "s1", "--record", records[1]])
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertIn("recorded 1 application(s)", b.stdout,
                          "the second unit of work recorded nothing")
            self.assertEqual(self._rows(root), 2)
            db = os.path.join(root, ".brothermode", "store.sqlite3")
            con = sqlite3.connect(db)
            try:
                linked = sorted(row[0] for row in con.execute(
                    "SELECT record_uuid FROM learning_applications"))
            finally:
                con.close()
            self.assertEqual(linked, sorted(records),
                             "each unit of work must own exactly one row")
            again = self._learn(root, ["apply", "--query", self.QUERY,
                                       "--session", "s1", "--record",
                                       records[1]])
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("1 already recorded", again.stdout)
            self.assertEqual(self._rows(root), 2, "re-run duplicated a row")

    def test_apply_now_prevents_the_ambiguity_it_used_to_only_disclose(self):
        """LOOP 4 CLOSED THIS BY PREVENTION INSTEAD OF DISCLOSURE.

        The old contract: with no --record there was nothing to key on beyond
        the shared task wording, so an existing row might belong to a different
        unit of work. That ambiguity could not be resolved, so it was DISCLOSED,
        with a NOTE naming the record the found row belonged to.

        The new contract is stronger. A bare apply cannot happen at all, so the
        ambiguous row can never be created in the first place, and there is
        nothing left to disclose. A prevented ambiguity beats a well-described
        one.

        NOTE FOR A LATER SESSION: the disclosure branch in bm_learn.py that
        prints "NOTE: an already recorded row for this task belongs to work
        record ..." is believed UNREACHABLE from `apply` now. It has not been
        confirmed unreachable from the deprecated `relevant` alias, so it is
        left in place rather than deleted on an assumption."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            env = dict(os.environ, BROTHERMODE_ROOT=root)
            c = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_store.py"), "claim",
                 "p5-owned"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(c.returncode, 0, c.stderr)
            rec = re.search(r"lifecycle ([0-9a-f]{32})", c.stdout).group(1)
            first = self._learn(root, ["apply", "--query", self.QUERY,
                                       "--session", "s1", "--record", rec])
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotIn("NOTE: an already recorded row", first.stdout)
            bare = self._learn(root, ["apply", "--query", self.QUERY,
                                      "--session", "s1"])
            self.assertEqual(bare.returncode, 2, bare.stdout)
            self.assertIn("requires a work identity", bare.stderr)
            self.assertEqual(
                self._rows(root), 1,
                "the refused bare apply must not have added an ambiguous row")

    def test_calibrated_the_old_optional_flag_reproduces_the_unrecorded_run(self):
        """CALIBRATION. Put the pre-fix contract back (recording opt-in, no
        session requirement) by driving the deprecated alias WITHOUT the flag,
        which is exactly the command SKILL.md used to name. It must still exit
        0 and still leave the table empty. If that stopped being true, the
        tests above would be passing for some other reason."""
        with tempfile.TemporaryDirectory() as root:
            self._rule(root)
            r = self._learn(root, ["relevant", "--query", self.QUERY])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("use the GitHub Desktop app", r.stdout)
            self.assertEqual(self._rows(root), 0,
                             "REINJECTION CHECK: the old flag-optional path no "
                             "longer reproduces the unrecorded run, so the "
                             "tests above are not calibrated to the defect")
            self.assertIn("DEPRECATED", r.stderr,
                          "the alias kept the old behaviour without saying so")

    def test_skill_md_names_apply_and_no_unrecorded_substantial_path(self):
        """The law is the thing an agent actually follows. If SKILL.md still
        told it to run a read-only command before substantial work, every fix
        above would be decoration.

        R2 (2026-08-07) moved the command semantics, exit codes, work
        identity, receipts and approval mechanics out of SKILL.md into
        references/learned-rules.md; SKILL.md keeps only the unconditional
        law, the apply invocation, and the routing pointer to that file."""
        FULL_APPLY_INVOCATION = (
            'python3 tools/bm_learn.py apply --query '
            '"<what you are about to do>" --session <session-id> '
            '(--record <work-uuid> | --new-record <name>)')
        with io.open(os.path.join(os.path.dirname(HERE), "SKILL.md"),
                     encoding="utf-8") as f:
            src = f.read()
        head = src.split("## Founder rules, before a SUBSTANTIAL task", 1)
        self.assertEqual(len(head), 2, "the founder-rules law moved or is gone")
        law = head[1].split("\n## ", 1)[0]
        self.assertIn("bm_learn.py apply --query", law)
        self.assertIn("--session", law)
        self.assertIn("references/learned-rules.md", law,
                      "the core no longer routes to the mechanics file")
        with io.open(os.path.join(os.path.dirname(HERE), "references",
                                  "learned-rules.md"), encoding="utf-8") as f:
            ref = f.read()
        for dead in ("bm_learn.py relevant --query",
                     "--record-applications"):
            for name, text in (("SKILL.md", law),
                               ("references/learned-rules.md", ref)):
                self.assertNotIn(dead, text,
                                 "%s still names %r, which records nothing "
                                 "unless a flag is remembered" % (name, dead))
        # lookup may be mentioned, but never as the substantial-work command.
        # That sentence moved to the reference along with the rest of the
        # command semantics.
        self.assertIn("NOT a substantial-work path", ref)
        # The apply invocation is deliberately dual-homed: an inline copy in
        # SKILL.md so the unconditional law survives even when no reference
        # is loaded, and the full command block in the reference. Pinning
        # both makes drift between them mechanically visible.
        self.assertIn(FULL_APPLY_INVOCATION, law,
                      "the full apply invocation drifted out of SKILL.md")
        self.assertIn(FULL_APPLY_INVOCATION, ref,
                      "the full apply invocation drifted out of "
                      "references/learned-rules.md")


class TestPostAuditLoopP7PureRanking(unittest.TestCase):
    """LOOP P7, the pure half. bm_learning must be able to rank WITH a BM25 map
    and WITHOUT one, and the without case has to be provably identical to the
    order it produced before the fast path existed."""

    RULE = {"rule_uuid": "a" * 32, "scope_type": "global", "scope_key": "",
            "state": "approved", "trigger_text": "when pushing to github",
            "action_text": "use the GitHub Desktop app",
            "because_text": "", "domain": ""}

    def _legacy_key(self, rule, query):
        """The pre-P7 sort key, written out by hand. If rank_key in lexical
        mode ever stops agreeing with this, the fallback has changed and the
        founder's retrieval order moved under them."""
        spec = bl.SCOPE_SPECIFICITY.index(rule["scope_type"])
        state = bl.INJECTABLE_STATES.index(rule["state"])
        rel = bl.lexical_overlap(query, rule["trigger_text"], rule["action_text"],
                                 rule["because_text"], rule["domain"],
                                 rule["scope_key"])
        return (spec, state, -rel, rule["rule_uuid"])

    def test_lexical_mode_orders_exactly_as_it_did_before_fts5(self):
        rules = [dict(self.RULE, rule_uuid=u * 32, scope_type=s,
                      state=st, action_text=a)
                 for u, s, st, a in (("a", "global", "approved", "use the app"),
                                     ("b", "project", "settled", "push with the app"),
                                     ("c", "global", "settled", "never bare push"),
                                     ("d", "domain", "confirmed", "ask first"))]
        for r in rules:
            if r["scope_type"] != "global":
                r["scope_key"] = "k"
        q = "pushing to github with the app"
        by_new = [r["rule_uuid"] for r in sorted(rules, key=lambda r: bl.rank_key(r, q))]
        by_old = [r["rule_uuid"] for r in sorted(rules, key=lambda r: self._legacy_key(r, q))]
        self.assertEqual(by_new, by_old)

    def test_bm25_outranks_lexical_overlap_but_never_scope_or_state(self):
        a = dict(self.RULE, rule_uuid="a" * 32)
        b = dict(self.RULE, rule_uuid="b" * 32)
        q = "pushing to github"
        # Same scope, same state: the better BM25 wins even though lexical
        # overlap is identical.
        scores = {a["rule_uuid"]: -0.0, b["rule_uuid"]: -5.0}
        self.assertLess(bl.rank_key(b, q, None, scores),
                        bl.rank_key(a, q, None, scores))
        # A more specific scope still wins regardless of BM25.
        narrow = dict(a, scope_type="project", scope_key="k")
        self.assertLess(bl.rank_key(narrow, q, None, scores),
                        bl.rank_key(b, q, None, scores))

    def test_bm25_component_flips_the_sign_once_and_survives_junk(self):
        self.assertEqual(bl.bm25_component(-2.5), 2.5)
        self.assertEqual(bl.bm25_component(0.0), 0.0)
        # A positive value is not a better match, it is nonsense; it must not
        # become a NEGATIVE component and silently sort last-but-one.
        self.assertEqual(bl.bm25_component(3.0), 0.0)
        for junk in (None, "", "x", float("nan")):
            self.assertEqual(bl.bm25_component(junk), 0.0)

    def test_the_match_expression_quotes_every_token(self):
        self.assertEqual(bl.fts_match_query("push github"), '"push" OR "github"')
        self.assertEqual(bl.fts_match_query(""), "")
        self.assertEqual(bl.fts_match_query("   ...   "), "")
        # Operators arrive as ordinary words or not at all, never as syntax.
        expr = bl.fts_match_query('github NEAR/2 "x* OR ^col:')
        self.assertNotIn("*", expr)
        self.assertNotIn("^", expr)
        self.assertNotIn(":", expr)
        self.assertTrue(all(part.startswith(chr(34)) and part.endswith(chr(34))
                            for part in expr.split(" OR ")))

    def test_explanation_names_its_components_and_never_lies_about_mode(self):
        q = "pushing to github"
        lex = bl.explain_rank(self.RULE, q)
        self.assertEqual(lex["mode"], "lexical")
        self.assertEqual(lex["bm25"], 0.0)
        fast = bl.explain_rank(self.RULE, q, None,
                               {self.RULE["rule_uuid"]: -1.25}, "fts5")
        self.assertEqual(fast["mode"], "fts5")
        self.assertEqual(fast["bm25"], 1.25)
        for key in ("scope", "state", "mode", "matched_terms", "relevance",
                    "bm25", "lexical_bonus"):
            self.assertIn(key, fast)
        # A rule with no FTS hit in an fts5 run still reports the run's mode.
        miss = bl.explain_rank(self.RULE, q, None, {}, "fts5")
        self.assertEqual(miss["mode"], "fts5")
        self.assertEqual(miss["bm25"], 0.0)
# ---------------------------------------------------------------------------
# Loop P9 fix round: the release gate's OWN code was never under test. Three
# reproduced defects are pinned here, each with the mutation that used to slip
# through.
# ---------------------------------------------------------------------------

_ta_spec = importlib.util.spec_from_file_location(
    "bm_test_all_under_test", os.path.join(HERE, "test_all.py"))
ta = importlib.util.module_from_spec(_ta_spec)
_ta_spec.loader.exec_module(ta)


def _inventory_of(case, text):
    """Run the CI inventory check against a workflow body of our own, by
    pointing the module at a temp file. Restored on cleanup."""
    d = tempfile.mkdtemp(prefix="bm-workflow-")
    case.addCleanup(shutil.rmtree, d, True)
    path = os.path.join(d, "tests.yml")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    old = ta.WORKFLOW
    ta.WORKFLOW = path
    case.addCleanup(setattr, ta, "WORKFLOW", old)
    return ta._ci_inventory()


_WF = """name: tests
jobs:
  suite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Configure git
        run: |
          git config --global user.email ci@example.com
          git config --global user.name ci
      - name: Run the fence hook suite
        run: python3 tools/test_bm_fence_hook.py
"""


class TestSBE2SilenceLimitMeasuresSilenceNotProgress(unittest.TestCase):
    """SBE2, 2026-08-16. The gate killed healthy suites and reported them as
    `FAIL 0 tests`, three times in one night, and every long run looked
    stalled to whoever was watching. The cause was never slowness: the pump
    read whole LINES while unittest writes one dot per test with NO newline,
    and the child line-buffered those dots because its stderr was a pipe. So
    a suite making steady progress delivered nothing to the reader, and the
    SILENCE limit measured a working suite as silent.

    This test is written against the shape of the real failure rather than
    the shape of the fix: a suite whose individual gaps are WELL under the
    limit but whose total runtime is well over it. That is exactly
    test_install.py, which passed alone in 770s and was killed at the 900s
    limit inside the gate. Calibrated against the pre-fix reader, which is
    reproduced inline below and which fails this fixture, because a
    regression test for a reader must prove it can still tell the two
    readers apart."""

    def _fixture(self):
        d = tempfile.mkdtemp(prefix="bm-dotsuite-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "dotsuite.py")
        lines = ["import time, unittest", "class T(unittest.TestCase):"]
        for i in range(6):
            lines.append("    def test_%02d(self): time.sleep(0.6)" % i)
        lines += ['if __name__ == "__main__":', "    unittest.main(verbosity=1)"]
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def _pre_fix_reader(self, path, silence_limit):
        """The reader as it stood before SBE2, verbatim in the two respects
        that mattered: no -u on the child, and readline in the pump."""
        import threading
        proc = subprocess.Popen(
            [sys.executable, path], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1)
        last = [time.time()]

        def _pump():
            for _line in iter(proc.stdout.readline, ""):
                last[0] = time.time()

        t = threading.Thread(target=_pump)
        t.daemon = True
        t.start()
        timed_out = False
        while True:
            if proc.poll() is not None:
                break
            if time.time() - last[0] > silence_limit:
                timed_out = True
                proc.kill()
                break
            time.sleep(0.1)
        return timed_out

    def test_a_working_suite_slower_than_the_limit_is_not_killed(self):
        path = self._fixture()
        ok, out, timed_out = ta._run_until_silent(path, silence_limit=1.5)
        self.assertFalse(timed_out,
                         "a suite whose gaps are 0.6s was killed by a 1.5s "
                         "SILENCE limit, so the limit is measuring total "
                         "runtime again rather than silence")
        self.assertTrue(ok, out)
        self.assertIn("Ran 6 tests", out,
                      "the captured text must still carry the counts the "
                      "parser downstream reads: %r" % out[-120:])

    def test_calibrated_the_pre_fix_reader_fails_this_same_fixture(self):
        """Without this, the test above could pass for the wrong reason
        (a fixture too easy to kill anything), and the project has a whole
        failure class about tests that pass for the wrong reason."""
        path = self._fixture()
        self.assertTrue(
            self._pre_fix_reader(path, silence_limit=1.5),
            "the pre-fix reader survived the fixture, so this fixture no "
            "longer reproduces SBE2 and proves nothing about the fix")


class TestLoopP9GateCiInventoryCountsExecutionNotMentions(unittest.TestCase):
    def test_a_real_step_counts(self):
        self.assertEqual(_inventory_of(self, _WF), {"test_bm_fence_hook.py"})

    def test_a_commented_out_step_does_not_count(self):
        text = _WF.replace(
            "      - name: Run the fence hook suite\n"
            "        run: python3 tools/test_bm_fence_hook.py\n",
            "      # - name: Run the fence hook suite\n"
            "      #   run: python3 tools/test_bm_fence_hook.py\n")
        self.assertEqual(_inventory_of(self, text), set())

    def test_a_step_gated_if_false_does_not_count(self):
        text = _WF.replace("      - name: Run the fence hook suite\n",
                           "      - name: Run the fence hook suite\n"
                           "        if: false\n")
        self.assertEqual(_inventory_of(self, text), set())

    def test_a_step_that_only_echoes_the_path_does_not_count(self):
        text = _WF.replace("run: python3 tools/test_bm_fence_hook.py",
                           "run: echo TODO tools/test_bm_fence_hook.py")
        self.assertEqual(_inventory_of(self, text), set())

    def test_a_comment_naming_a_deleted_suite_is_not_a_phantom(self):
        # The reverse direction: a historical note used to hard block the gate
        # at exit 2 with zero tests run.
        text = _WF.replace(
            "jobs:\n",
            "jobs:\n  # History: tools/test_legacy_v1.py was deleted in 1.9.0.\n")
        self.assertEqual(_inventory_of(self, text), {"test_bm_fence_hook.py"})

    def test_a_platform_conditional_step_still_counts(self):
        # Only `if: false` disqualifies a step. A step that runs on one leg
        # runs in CI.
        text = _WF.replace("      - name: Run the fence hook suite\n",
                           "      - name: Run the fence hook suite\n"
                           "        if: matrix.os == 'ubuntu-latest'\n")
        self.assertEqual(_inventory_of(self, text), {"test_bm_fence_hook.py"})

    def test_this_repository_agrees_with_its_own_workflow(self):
        ci = ta._ci_inventory()
        if ci is None:
            self.skipTest("no workflow in this checkout")
        for name in ta.SUITES:
            self.assertIn(name, ci,
                          "%s is in the local gate but no CI step runs it" % name)


class TestLoopP9GateLockDoesNotStealFromLiveRuns(unittest.TestCase):
    def setUp(self):
        # Every test here works on a PRIVATE lock path. The real one may be
        # held right now by the test_all run that launched this suite, and a
        # test that fights the live gate lock is a test that reports the gate
        # as broken whenever the gate is working.
        self._dir = tempfile.mkdtemp(prefix="bm-lock-")
        self.addCleanup(shutil.rmtree, self._dir, True)
        private = os.path.join(self._dir, "private-gate.lock")
        old = ta.lock_path
        ta.lock_path = lambda: private
        self.addCleanup(setattr, ta, "lock_path", old)
        self.private = private

    def _lock(self, body):
        path = os.path.join(self._dir, "gate.lock")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_a_live_holder_is_never_stale_however_old(self):
        # 3600s used to mean "dead". A full local gate is exactly 900s x 4
        # suites, and the CI gate job allows 1200s x 4.
        path = self._lock("%d %.3f slow but alive\n"
                          % (os.getpid(), time.time() - 100000.0))
        self.assertFalse(ta._lock_is_stale(path),
                         "a running process must never be declared stale")

    def test_a_dead_holder_is_stale(self):
        if os.name != "posix":
            self.skipTest("pid liveness probing is posix only here")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        path = self._lock("%d %.3f dead\n" % (dead.pid, time.time()))
        self.assertTrue(ta._lock_is_stale(path))

    def test_release_never_removes_a_lock_this_process_did_not_take(self):
        path = self._lock("999999 0 somebody else\n")
        ta.release_gate_lock(path, quiet=True)
        self.assertTrue(os.path.exists(path),
                        "releasing a lock we never took would let a third run in")

    def test_release_never_removes_a_lock_that_was_stolen_mid_run(self):
        old_env = os.environ.pop(ta.LOCK_ENV, None)
        if old_env is not None:
            self.addCleanup(os.environ.__setitem__, ta.LOCK_ENV, old_env)
        handle = ta.acquire_gate_lock(timeout=5, owner="p9 test", quiet=True)
        self.assertIsNotNone(handle)
        self.addCleanup(lambda: os.path.exists(handle) and os.remove(handle))
        with io.open(handle, "w", encoding="utf-8") as f:
            f.write("424242 1.0 a thief\n")
        ta.release_gate_lock(handle, quiet=True)
        self.assertTrue(os.path.exists(handle),
                        "the thief's lock must survive our release")

    def test_a_bogus_lock_env_value_does_not_silently_disable_the_lock(self):
        old_env = os.environ.get(ta.LOCK_ENV)
        os.environ[ta.LOCK_ENV] = "1"
        if old_env is None:
            self.addCleanup(os.environ.pop, ta.LOCK_ENV, None)
        else:
            self.addCleanup(os.environ.__setitem__, ta.LOCK_ENV, old_env)
        handle = ta.acquire_gate_lock(timeout=5, owner="p9 test", quiet=True)
        self.addCleanup(ta.release_gate_lock, handle, True)
        self.assertIsNotNone(
            handle,
            "exporting the variable used to turn mutual exclusion off silently")


class TestLoopP9NoSuiteMutatesThisCheckout(unittest.TestCase):
    def test_no_suite_renames_a_module_aside(self):
        # A per-suite timeout kills the child with SIGKILL, which runs no
        # finally clause. Any test that moves a file in this checkout and puts
        # it back in a finally can therefore delete a production module.
        # The needle is assembled at runtime so this scan cannot report its own
        # source line, which is the only false positive it can have.
        needle = ".moved" + "-for"
        offenders = []
        for name in sorted(os.listdir(HERE)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            for i, line in enumerate(_read(os.path.join(HERE, name)).splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if needle in line:
                    offenders.append("%s:%d" % (name, i))
        self.assertEqual(offenders, [],
                         "these lines move a file aside inside the checkout: %s"
                         % offenders)


class TestRf5GateReceiptIsWrittenAndFailsOpen(unittest.TestCase):
    """RF-5 (docs/handover/2026-08-11-morning/03-RULES-AND-PROCESS-FIXES.md,
    founder decision 12): the gate writes a machine-readable receipt to
    .brothermode/gate-receipt.json instead of every page and handover
    hand-quoting the summary line beside a hand-copied SHA."""

    def _init_repo(self, root):
        for cmd in (["git", "init", "-q", "."],
                   ["git", "config", "user.email", "t@example.com"],
                   ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with io.open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                           stdout=subprocess.PIPE, universal_newlines=True)
        return r.stdout.strip()

    def _receipt_path(self, root):
        return os.path.join(root, ".brothermode", "gate-receipt.json")

    def test_receipt_fields_match_a_clean_repo(self):
        with tempfile.TemporaryDirectory() as root:
            sha = self._init_repo(root)
            results = [("test_bm_docs.py", True, 12, 1, 0.5, "OK"),
                       ("test_bm_store.py", True, 30, 0, 1.5, "OK")]
            ta.write_gate_receipt(root, results, 2, 2.0, 0)
            with io.open(self._receipt_path(root), encoding="utf-8") as fh:
                receipt = json.load(fh)
            self.assertEqual(receipt["sha"], sha)
            self.assertIs(receipt["tree_dirty"], False)
            self.assertEqual(receipt["tests_total"], 42)
            self.assertEqual(receipt["suites_total"], 2)
            self.assertEqual(receipt["skipped"], 1)
            self.assertEqual(receipt["exit_code"], 0)
            self.assertAlmostEqual(receipt["wall_seconds"], 2.0)
            self.assertRegex(receipt["written_at"],
                             r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_a_dirty_tree_is_reported_true(self):
        with tempfile.TemporaryDirectory() as root:
            self._init_repo(root)
            with io.open(os.path.join(root, "f.txt"), "a", encoding="utf-8") as fh:
                fh.write("y\n")
            ta.write_gate_receipt(root, [], 0, 0.1, 1)
            with io.open(self._receipt_path(root), encoding="utf-8") as fh:
                receipt = json.load(fh)
            self.assertIs(receipt["tree_dirty"], True)
            self.assertEqual(receipt["exit_code"], 1)

    def test_a_non_git_directory_fails_open_and_writes_nothing(self):
        # Fail open (the RF-5 spec): git missing or the directory not being a
        # checkout must warn and never raise, and must never leave a partial
        # or wrong receipt behind.
        with tempfile.TemporaryDirectory() as root:
            ta.write_gate_receipt(root, [], 0, 0.1, 0)
            self.assertFalse(
                os.path.exists(self._receipt_path(root)),
                "a git failure must not produce a partial receipt")

    def test_the_receipt_directory_is_gitignored(self):
        text = _read(os.path.join(HERE, "..", ".gitignore"))
        self.assertIn(".brothermode/", text,
                     "the receipt directory must stay out of the tracked tree")


# scripts/bm_commit_msg_hook.py as a module object, same shape used for
# bm_telemetry, bm_store, bm_threads and bm_learn above: a pure-function test
# drives check_message() directly, in-process, and a behavioral test drives
# the real CLI as a subprocess the way a real git commit-msg hook would.
_commit_hook_spec = importlib.util.spec_from_file_location(
    "bm_commit_msg_hook",
    os.path.join(HERE, "..", "scripts", "bm_commit_msg_hook.py"))
commit_hook = importlib.util.module_from_spec(_commit_hook_spec)
_commit_hook_spec.loader.exec_module(commit_hook)


class TestRf1CommitMsgHookRefusesForbiddenMessages(unittest.TestCase):
    """RF-1 (docs/handover/2026-08-11-morning/03-RULES-AND-PROCESS-FIXES.md,
    founder decision 12): scripts/bm_commit_msg_hook.py refuses a commit
    message this repo's recorded policy forbids BEFORE the commit lands: no
    Co-Authored-By trailer, ever (repos.md, founder decision 2026-08-10:
    "No Claude co-author trailer in this repo, founder rule: only Khalil
    Maaouni as creator"), plus the standing no-em/no-en-dash rule.

    NO NEW SUITES ENTRY: scripts/*.py scripts do not get their own
    tools/test_*.py file in this repo (doctor.py's own subprocess tests live
    inline in tools/test_bm_consent.py, not in a test_doctor.py; grep
    confirms no test_doctor.py exists). Adding a new SUITES entry here would
    need a matching step in .github/workflows/tests.yml, or test_all.py's
    own CI inventory check (_inventory_gate, read before writing this) would
    REFUSE the whole gate on the very next run. So these tests live in the
    already-registered, already-CI-wired tools/test_bm.py instead, the same
    placement doctor.py's own tests use."""

    HOOK = os.path.join(HERE, "..", "scripts", "bm_commit_msg_hook.py")

    def _write(self, text):
        d = tempfile.mkdtemp(prefix="bm-commit-msg-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "MSG")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _run(self, args):
        return subprocess.run([sys.executable, self.HOOK] + args,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_a_clean_message_exits_zero(self):
        path = self._write("a clean commit message\n")
        r = self._run([path])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_em_dash_exits_one_and_names_the_line(self):
        path = self._write("first line ok\na line with an em dash \u2014 in it\n")
        r = self._run([path])
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 2", r.stderr)
        self.assertIn("em dash", r.stderr)

    def test_an_en_dash_exits_one_and_names_the_line(self):
        path = self._write("a line with an en dash \u2013 in it\n")
        r = self._run([path])
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 1", r.stderr)
        self.assertIn("en dash", r.stderr)

    def test_a_co_authored_by_trailer_exits_one(self):
        path = self._write("a message\n\nCo-Authored-By: Someone <x@example.com>\n")
        r = self._run([path])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Co-Authored-By", r.stderr)
        self.assertIn("line 3", r.stderr)

    def test_missing_argument_exits_two(self):
        r = self._run([])
        self.assertEqual(r.returncode, 2)

    def test_check_message_is_a_pure_function_used_directly(self):
        self.assertIsNone(commit_hook.check_message("clean\n"))
        self.assertIsNotNone(
            commit_hook.check_message("a bad \u2014 dash\n"))


class TestM1InstallShapeDoctorCheck(unittest.TestCase):
    """M1(a) (docs/plan/QUEUE.json): scripts/doctor.py's install_shape
    check. Measured failure this check exists to catch
    (docs/NORTH-STAR-CHAIN.md finding 1): a skill-directory clone at
    ~/.claude/skills/brothermode with no fence hook wired anywhere (its
    own hooks.json missing the reference, settings.json carrying no
    PreToolUse entry either) is a STRANDED install that nothing existing
    catches: check_duplicate_install's own question is "is more than one
    method wired", and it answers a bare PASS for "neither is wired" too.

    NO NEW SUITES ENTRY, same reasoning as
    TestRf1CommitMsgHookRefusesForbiddenMessages above: scripts/*.py
    checks are tested inline in tools/test_bm_consent.py, not a
    dedicated test_doctor.py, and this class follows the identical
    placement for the same registry reason (a new SUITES entry needs a
    matching CI step, or test_all.py's own inventory gate refuses the
    run)."""

    DOCTOR = os.path.join(HERE, "..", "scripts", "doctor.py")

    def _env(self, home):
        env = dict(os.environ)
        env["HOME"] = home
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
                  "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
            env.pop(k, None)
        return env

    def _run_doctor(self, home, project, settings):
        return subprocess.run(
            [sys.executable, self.DOCTOR, "--settings", settings, "--json"],
            cwd=project, env=self._env(home),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)

    def _checks(self, stdout):
        payload = json.loads(stdout)
        return {c["key"]: c for c in payload["checks"]}

    def test_stranded_skill_dir_clone_with_no_wired_hook_fails_named(self):
        """REPRODUCED pre-fix: no install_shape key existed at all, so
        this exact scenario (a skill-directory clone sitting on disk with
        nothing loading it) produced no failing check anywhere in
        doctor's output; check_duplicate_install read "neither method is
        wired" as a PASS. Looking up checks["install_shape"] against a
        pre-fix doctor.py raises KeyError outright."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            # A skill-directory clone whose own hooks.json exists but
            # names no fence hook: the exact stranded shape measured.
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            os.makedirs(os.path.join(skill_dir, "hooks"))
            with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                         "w", encoding="utf-8") as fh:
                json.dump({"hooks": {}}, fh)
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertIn("install_shape", checks,
                          "install_shape check is missing from doctor's "
                          "own registry")
            self.assertEqual(checks["install_shape"]["status"], "FAIL",
                             checks["install_shape"]["message"])
            self.assertIn("STRANDED", checks["install_shape"]["message"])
            self.assertIn(os.path.join(".claude", "skills", "brothermode"),
                          checks["install_shape"]["message"])

    def test_no_install_evidence_at_all_is_no_data_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertEqual(checks["install_shape"]["status"], "SKIP")
            self.assertIn("NO-DATA", checks["install_shape"]["message"])

    def test_working_clone_with_a_real_fence_hook_wired_is_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            fence = os.path.join(tmp, "clone_tools", "bm_fence_hook.py")
            os.makedirs(os.path.dirname(fence))
            with io.open(fence, "w", encoding="utf-8") as fh:
                fh.write("# stub, never executed by this test\n")
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                    "hooks": [{"type": "command",
                               "command": "python3 " + fence,
                               "timeout": 10}]}]}}, fh)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertEqual(checks["install_shape"]["status"], "PASS",
                             checks["install_shape"]["message"])


class TestM17FenceLivenessAgainstRealStore(unittest.TestCase):
    """M17 (docs/plan/QUEUE.json, family evidence-integrity): scripts/
    doctor.py's fence liveness check used to prove liveness only against a
    THROWAWAY project's store, built and read back by the very same
    bm_store.py code sitting beside the wired hook. That loop can never be
    behind itself, so it could never catch the one gap that matters most: a
    wired hook whose own bm_store.py is older than the schema THIS
    PROJECT'S real store is already at. Measured on 2026-08-20: doctor
    reported the fence check PASS while the installed hook's bm_store.py
    understood at most schema 20 and this project's real store was already
    at schema 21, so the installed bm_store.py refused to open it
    (schema-ahead) and the fence hook's own documented fail-open behavior
    let every write in this repository through unfenced.

    NO NEW SUITES ENTRY, same reasoning as TestM1InstallShapeDoctorCheck
    above: scripts/*.py checks are tested inline in an already-registered
    tools/test_*.py file, never a dedicated test_doctor.py."""

    DOCTOR = os.path.join(HERE, "..", "scripts", "doctor.py")

    def _env(self, home):
        env = dict(os.environ)
        env["HOME"] = home
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
                  "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
            env.pop(k, None)
        return env

    def _run_doctor(self, home, project, settings):
        return subprocess.run(
            [sys.executable, self.DOCTOR, "--settings", settings, "--json"],
            cwd=project, env=self._env(home),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)

    def _checks(self, stdout):
        payload = json.loads(stdout)
        return {c["key"]: c for c in payload["checks"]}

    def _old_install_one_schema_behind(self, tmp):
        """A real, working copy of every tools/*.py file (dependency
        closure, the same technique DoctorStrictAndSummaryCase in
        test_bm_consent.py uses to build a wired-and-live fence fixture),
        except this copy's own bm_store.py has its SCHEMA_VERSION patched
        down by exactly one: a real, functioning hook that understands one
        schema less than the code sitting beside it in this repo today."""
        tools_dir = os.path.join(tmp, "old_install", "tools")
        os.makedirs(tools_dir)
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and not name.startswith("test_"):
                shutil.copy2(os.path.join(HERE, name), os.path.join(tools_dir, name))
        store_copy = os.path.join(tools_dir, "bm_store.py")
        with io.open(store_copy, encoding="utf-8") as fh:
            src = fh.read()
        old_line = "SCHEMA_VERSION = %d" % bs.SCHEMA_VERSION
        new_line = "SCHEMA_VERSION = %d" % (bs.SCHEMA_VERSION - 1)
        self.assertIn(old_line, src,
                      "fixture assumption broke: tools/bm_store.py no "
                      "longer defines %r verbatim" % old_line)
        with io.open(store_copy, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old_line, new_line, 1))
        return tools_dir

    def test_real_store_ahead_of_wired_hook_fails_not_passes(self):
        """REPRODUCED pre-fix: the throwaway-store simulation this check
        runs is self-consistent by construction (the same code creates and
        then reads the store), so it passes regardless of this gap; doctor
        reported the fence PASS while the wired hook could not open this
        project's actual store at all. FAIL, or a SKIP naming why it could
        not tell, are both acceptable; PASS is not, per this file's own
        NO-DATA discipline (never silently PASS what was not checked)."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            old_tools = self._old_install_one_schema_behind(tmp)
            # THIS PROJECT'S OWN REAL STORE, created by the CURRENT,
            # unmodified tools/bm_store.py beside this test file, one
            # schema ahead of the wired hook's copy above by construction.
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_store.py"), "init"],
                cwd=project, env=self._env(home),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=300)
            self.assertEqual(r.returncode, 0,
                             "fixture setup: could not init the real "
                             "project store: %s" % (r.stderr or r.stdout))
            fence = os.path.join(old_tools, "bm_fence_hook.py")
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": "python3 " + fence,
                              "timeout": 10}]}]}}, fh)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertNotEqual(checks["fence"]["status"], "PASS",
                                "doctor reported the fence live while the "
                                "wired hook's own store code cannot open "
                                "this project's real store: %s"
                                % checks["fence"]["message"])
            self.assertEqual(checks["fence"]["status"], "FAIL",
                             checks["fence"]["message"])
            self.assertIn("schema", checks["fence"]["message"].lower())

    def test_subdirectory_cwd_still_finds_the_real_store(self):
        """REPRODUCED pre-fix (reviewer defect B3): the real-store check
        used real_root = os.getcwd(), so a doctor run from ANY
        subdirectory of the project (project/docs/, say) looked for
        docs/.brothermode/store.sqlite3, found nothing, and silently
        added no problem at all: the identical estate that FAILs from the
        project root PASSed from a subdirectory. Same fixture as
        test_real_store_ahead_of_wired_hook_fails_not_passes above
        (schema-behind wired hook, current-schema real store), run with
        cwd inside a subdirectory instead of the project root: the fix
        must resolve the real root honestly and report the SAME FAIL,
        not go silent."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            subdir = os.path.join(project, "docs")
            os.makedirs(home)
            os.makedirs(subdir)
            old_tools = self._old_install_one_schema_behind(tmp)
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_store.py"), "init"],
                cwd=project, env=self._env(home),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=300)
            self.assertEqual(r.returncode, 0,
                             "fixture setup: could not init the real "
                             "project store: %s" % (r.stderr or r.stdout))
            fence = os.path.join(old_tools, "bm_fence_hook.py")
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": "python3 " + fence,
                              "timeout": 10}]}]}}, fh)
            # ONLY DIFFERENCE from the root-run test above: doctor's cwd is
            # a subdirectory of the project, not the project root itself.
            r = self._run_doctor(home, subdir, settings)
            checks = self._checks(r.stdout)
            self.assertNotEqual(checks["fence"]["status"], "PASS",
                                "doctor reported the fence live from a "
                                "SUBDIRECTORY of the project while the "
                                "wired hook's own store code cannot open "
                                "this project's real store, the exact "
                                "estate that FAILs from the project root: "
                                "%s" % checks["fence"]["message"])
            self.assertEqual(checks["fence"]["status"], "FAIL",
                             checks["fence"]["message"])
            self.assertIn("schema", checks["fence"]["message"].lower())

    def _wire_current_fence(self, settings):
        """A normal, CURRENT, fully working fence hook (not the
        one-schema-behind fixture above): defects B4 and B5 below are
        about the STORE PATH SHAPE and the INTERPRETER used, not a schema
        gap, so the ordinary tools/*.py beside this test file is the
        wired hook."""
        fence = os.path.join(HERE, "bm_fence_hook.py")
        with io.open(settings, "w", encoding="utf-8") as fh:
            json.dump({"hooks": {"PreToolUse": [{
                "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                "hooks": [{"type": "command", "command": "python3 " + fence,
                          "timeout": 10}]}]}}, fh)
        return fence

    def test_store_path_as_a_directory_is_reported_not_skipped(self):
        """REPRODUCED pre-fix (reviewer defect B4): os.path.isfile()
        returns False for a PATH that exists but is a directory, so the
        pre-fix check treated "store.sqlite3 is a directory" identically
        to "no store here at all" and silently added no problem, while
        the real wired hook hits that same broken path on every real
        write and takes its no-store fail-open path. A broken store must
        be reported, not quietly waved through as "nothing to see"."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            brothermode_dir = os.path.join(project, ".brothermode")
            os.makedirs(brothermode_dir)
            os.makedirs(os.path.join(brothermode_dir, "store.sqlite3"))
            settings = os.path.join(tmp, "settings.json")
            self._wire_current_fence(settings)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertEqual(checks["fence"]["status"], "FAIL",
                             "a store path that is a DIRECTORY must be "
                             "reported, not silently treated as 'no "
                             "store': %s" % checks["fence"]["message"])
            self.assertIn("not a database file",
                          checks["fence"]["message"].lower())

    def test_store_path_as_a_dangling_symlink_is_reported_not_skipped(self):
        """REPRODUCED pre-fix (reviewer defect B4), second shape named in
        the review: a DANGLING SYMLINK at the store path also fails
        os.path.isfile() (it follows the link and finds nothing), so it
        was silently read as "no store to inspect" exactly like the
        directory case above."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            brothermode_dir = os.path.join(project, ".brothermode")
            os.makedirs(brothermode_dir)
            os.symlink(os.path.join(brothermode_dir, "does-not-exist"),
                      os.path.join(brothermode_dir, "store.sqlite3"))
            settings = os.path.join(tmp, "settings.json")
            self._wire_current_fence(settings)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            self.assertEqual(checks["fence"]["status"], "FAIL",
                             "a store path that is a DANGLING SYMLINK "
                             "must be reported, not silently treated as "
                             "'no store': %s" % checks["fence"]["message"])
            self.assertIn("not a database file",
                          checks["fence"]["message"].lower())

    def test_real_store_check_uses_the_wired_interpreter_not_sys_executable(self):
        """REPRODUCED pre-fix (reviewer defect B5): the real-store verify
        subprocess call used sys.executable instead of command_words[0],
        the interpreter the WIRED command actually names, the same
        command_words the deny/allow simulation already runs faithfully.
        Proof: a shim interpreter that logs every argv it is called with
        and always exits 17, wired as the fence command in place of
        python3. The deny/allow simulation above calls the shim 8 times
        (4 write tools x deny-then-allow) and every one is logged; the
        pre-fix real-store step quietly called sys.executable instead, so
        'verify' never appeared in the shim's log even though a real,
        healthy, current-schema project store sits right there for it to
        check."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_store.py"), "init"],
                cwd=project, env=self._env(home),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=300)
            self.assertEqual(r.returncode, 0,
                             "fixture setup: could not init the real "
                             "project store: %s" % (r.stderr or r.stdout))

            shim_log = os.path.join(tmp, "shim-log.txt")
            shim_path = os.path.join(tmp, "shim_interpreter.py")
            with io.open(shim_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import sys\n"
                    "with open(%r, \"a\") as fh:\n"
                    "    fh.write(repr(sys.argv) + \"\\n\")\n"
                    "sys.exit(17)\n" % shim_log)
            st = os.stat(shim_path)
            os.chmod(shim_path,
                     st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            fence = os.path.join(HERE, "bm_fence_hook.py")
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command",
                              "command": shim_path + " " + fence,
                              "timeout": 10}]}]}}, fh)
            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)

            self.assertTrue(os.path.isfile(shim_log),
                            "fixture assumption broke: the shim was "
                            "never invoked at all")
            with io.open(shim_log, encoding="utf-8") as fh:
                log_text = fh.read()
            self.assertIn("verify", log_text,
                          "the real-store check ran the WRONG "
                          "interpreter: the wired command names %s, and "
                          "the deny/allow simulation above already calls "
                          "it faithfully (its own log lines are proof), "
                          "but no logged call carries 'verify', meaning "
                          "the real-store step used sys.executable "
                          "instead of the wired interpreter, silently "
                          "bypassing the shim entirely. Log:\n%s"
                          % (shim_path, log_text))
            # Incidental, not the discriminating assertion (the deny/allow
            # simulation already fails loudly against this shim on its
            # own): still worth pinning down so a future change cannot
            # silently soften it.
            self.assertEqual(checks["fence"]["status"], "FAIL",
                             checks["fence"]["message"])


class TestM20WiredHookRedirectionShellSemantics(unittest.TestCase):
    """M20 (docs/plan/QUEUE.json, family evidence-integrity): scripts/
    doctor.py's blocked-write simulation ran the wired hook command as an
    argv LIST (subprocess.run([...]), no shell), so a wired command
    carrying a shell redirection was never actually redirected. shlex.split
    keeps a token like '>' or '>/tmp/log' as a literal extra word; a script
    that tolerates unknown extra argv just ignores it and prints its real
    output straight onto the pipe doctor reads, so a command that would
    swallow its own stdout under a REAL shell looked exactly like a command
    with no redirection at all, and doctor reported the fence live. Claude
    Code hands this exact command STRING to a shell (established at
    test_bm_consent.py's test_no_wired_command_of_any_module_writes_before_
    consent and reused unmodified at tools/test_bm_hookperf.py's
    run_real()); under that real shell the redirection is honoured, so
    both the deny ask and the allow ask see EMPTY stdout, indistinguishable
    from each other, and doctor must not call that a live fence.

    Reproduced 2026-08-21; cross-family debate finding 6, BLOCKER."""

    DOCTOR = os.path.join(HERE, "..", "scripts", "doctor.py")
    INTRUDER_SESSION = "bm-doctor-intruder"

    def _env(self, home):
        env = dict(os.environ)
        env["HOME"] = home
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
                  "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
            env.pop(k, None)
        return env

    def _run_doctor(self, home, project, settings):
        return subprocess.run(
            [sys.executable, "-B", self.DOCTOR, "--settings", settings,
             "--json"],
            cwd=project, env=self._env(home),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300)

    def _checks(self, stdout):
        payload = json.loads(stdout)
        return {c["key"]: c for c in payload["checks"]}

    def _tools_dir_with_stub_fence(self, tmp):
        """A real, working copy of every tools/*.py file (the dependency
        closure bm_store.py's own lazy imports need, same technique
        TestM17FenceLivenessAgainstRealStore uses), with bm_fence_hook.py
        replaced by a STUB that is deliberately tolerant of extra argv: it
        never looks at sys.argv for the hook path, only at the JSON on
        stdin, exactly the shape a real hook script can have and exactly
        why the pre-fix argv-list execution could not be trusted to expose
        a redirection by accident (a strict CLI erroring on the stray
        token would hide this bug behind a different, unrelated FAIL)."""
        tools_dir = os.path.join(tmp, "tools")
        os.makedirs(tools_dir)
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and not name.startswith("test_"):
                shutil.copy2(os.path.join(HERE, name),
                             os.path.join(tools_dir, name))
        fence = os.path.join(tools_dir, "bm_fence_hook.py")
        stub = (
            "import json, sys\n"
            "def main():\n"
            "    argv = sys.argv[1:]\n"
            "    if argv and argv[0] == 'session-label':\n"
            "        print('stub-owner-label')\n"
            "        return 0\n"
            "    payload = json.loads(sys.stdin.read() or '{}')\n"
            "    if payload.get('session_id') == %r:\n"
            "        print(json.dumps({'hookSpecificOutput': {\n"
            "            'hookEventName': 'PreToolUse',\n"
            "            'permissionDecision': 'deny',\n"
            "            'permissionDecisionReason': 'stub deny'}}))\n"
            "    return 0\n"
            "sys.exit(main())\n"
        ) % (self.INTRUDER_SESSION,)
        with io.open(fence, "w", encoding="utf-8") as fh:
            fh.write(stub)
        return tools_dir, fence

    def test_redirected_wired_command_is_not_a_false_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            _tools_dir, fence = self._tools_dir_with_stub_fence(tmp)
            sink = os.path.join(tmp, "swallowed.log")
            command = "python3 %s > %s" % (fence, sink)
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                    "hooks": [{"type": "command", "command": command,
                              "timeout": 10}]}]}}, fh)

            r = self._run_doctor(home, project, settings)
            checks = self._checks(r.stdout)
            fence_check = checks["fence"]
            self.assertEqual(
                fence_check["status"], "FAIL",
                "wired command %r carries a redirection a real shell would "
                "honour, swallowing the fence's stdout so Claude Code could "
                "never see a deny; doctor must not report this a live "
                "fence: %s" % (command, fence_check["message"]))
            self.assertIn("not enforcing", fence_check["message"],
                         fence_check["message"])


class TestM25BlockedWriteSimulationLeavesNoBytecodeCache(unittest.TestCase):
    """M25 (docs/plan/QUEUE.json, family provenance): scripts/doctor.py's
    module docstring for CHECK 1 promises the blocked-write simulation is
    harmless in the strict sense: 'every file it creates lives under a
    fresh mkdtemp directory that is removed at the end'. That promise was
    not kept. blocked_write_simulation() runs the wired hook and the store
    CLI as subprocesses whose SCRIPT PATH is the real, installed tools_dir,
    not the throwaway mkdtemp root, and both bm_fence_hook.py (which
    lazily loads bm_store.py) and bm_store.py (which lazily loads
    bm_autosave.py, bm_telemetry.py, and others) do it with
    importlib.util.spec_from_file_location() plus exec_module(), the
    ordinary SourceFileLoader path, which writes a __pycache__/*.pyc
    beside the file it just imported the moment PYTHONDONTWRITEBYTECODE is
    unset and that directory is writable.

    Measured pre-fix with an isolated fixture (cwd outside any git
    ancestry, so doctor's separate _verify_real_store() step, a different
    function and out of scope here, takes its own NO-DATA path and spawns
    nothing of its own): bm_autosave, bm_fence_hook, bm_store and
    bm_telemetry each left a .pyc beside a fixture install whose
    __pycache__ started empty and whose fence check itself still reported
    PASS throughout. Fixed by setting PYTHONDONTWRITEBYTECODE=1 in the env
    blocked_write_simulation hands its own subprocesses, beside the other
    env mutations already there.

    NO NEW SUITES ENTRY, same reasoning as TestM1InstallShapeDoctorCheck
    and TestM17FenceLivenessAgainstRealStore above: scripts/*.py checks
    are tested inline in an already-registered tools/test_*.py file."""

    DOCTOR = os.path.join(HERE, "..", "scripts", "doctor.py")

    def _env(self, home):
        """Deliberately the OPPOSITE of TestM17FenceLivenessAgainstRealStore's
        _env(): that helper SETS PYTHONDONTWRITEBYTECODE=1 for its own
        subprocess-hygiene reasons, and that would inherit into doctor.py's
        own env = dict(os.environ) and mask this exact defect from this
        test. M25 is precisely about the var being UNSET, so this pops it
        rather than sets it, in case it is already set in the ambient
        shell running the suite."""
        env = dict(os.environ)
        env["HOME"] = home
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
                  "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
            env.pop(k, None)
        return env

    def _fixture_install(self, tmp):
        """A real, working copy of every tools/*.py file (dependency
        closure), the same technique TestM17FenceLivenessAgainstRealStore's
        _old_install_one_schema_behind above uses, minus the schema patch:
        M25 is about bytecode caching, not schema drift, so this fixture
        stays at the current schema, unmodified."""
        tools_dir = os.path.join(tmp, "fixture_install", "tools")
        os.makedirs(tools_dir)
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and not name.startswith("test_"):
                shutil.copy2(os.path.join(HERE, name), os.path.join(tools_dir, name))
        return tools_dir

    def _pyc_files(self, tools_dir):
        cache_dir = os.path.join(tools_dir, "__pycache__")
        if not os.path.isdir(cache_dir):
            return []
        return sorted(n for n in os.listdir(cache_dir) if n.endswith(".pyc"))

    def test_no_bytecode_cache_appears_beside_the_installed_hook(self):
        """REPRODUCED pre-fix (queue M25): running the fence simulation
        against a fixture install, with PYTHONDONTWRITEBYTECODE unset and
        cwd outside any git ancestry (so the separate real-store step
        finds no project to verify and spawns nothing of its own), left
        4 .pyc files under the fixture's own __pycache__/, beside the
        installed hook and store, not under any mkdtemp directory. FAIL is
        the honest pre-fix outcome for this assertion; the CHECK 1
        docstring's promise is that this list is always empty."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            tools_dir = self._fixture_install(tmp)
            self.assertEqual(self._pyc_files(tools_dir), [],
                             "fixture assumption broke: a __pycache__ "
                             "already exists beside a freshly copied "
                             "fixture install before doctor even ran")
            fence = os.path.join(tools_dir, "bm_fence_hook.py")
            settings = os.path.join(tmp, "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": "python3 " + fence,
                              "timeout": 10}]}]}}, fh)
            r = subprocess.run(
                [sys.executable, self.DOCTOR, "--settings", settings, "--json"],
                cwd=project, env=self._env(home),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=300)
            output_tail = (r.stderr or r.stdout or "")[:500]
            payload = json.loads(r.stdout)
            checks = {c["key"]: c for c in payload["checks"]}
            self.assertIn("fence", checks,
                          "fixture assumption broke: no 'fence' check in "
                          "doctor's --json output: %s" % output_tail)
            cache_files = self._pyc_files(tools_dir)
            self.assertEqual(cache_files, [],
                             "BLOCKED-WRITE SIMULATION WROTE OUTSIDE ITS "
                             "OWN TEMPORARY DIRECTORY: found %r beside the "
                             "installed hook at %s, contradicting the "
                             "CHECK 1 docstring's promise that every file "
                             "the simulation creates lives under a fresh "
                             "mkdtemp directory removed at the end. The "
                             "fence check itself reported %s."
                             % (cache_files, os.path.join(tools_dir, "__pycache__"),
                                checks["fence"]["status"]))


_migrate_install_spec = importlib.util.spec_from_file_location(
    "migrate_install", os.path.join(HERE, "..", "scripts", "migrate_install.py"))
migrate_install = importlib.util.module_from_spec(_migrate_install_spec)
_migrate_install_spec.loader.exec_module(migrate_install)


class TestM1MigrateInstallScript(unittest.TestCase):
    """M1(b) (docs/plan/QUEUE.json): scripts/migrate_install.py, the
    scriptable repair for the stranded-clone shape scripts/doctor.py's
    install_shape check (above) now detects. NEVER runs against the real
    machine here: every scenario below builds its own throwaway home
    under tempfile.TemporaryDirectory() and passes it through --home
    (CLI tests) or straight to detect() (in-process detector tests), the
    same discipline TestM1InstallShapeDoctorCheck above uses for
    doctor.py.

    NO NEW SUITES ENTRY, same reasoning as the two classes above:
    scripts/*.py gets tested inline in an already-registered
    tools/test_*.py file, never its own test_migrate_install.py (a new
    SUITES entry needs a matching CI step or test_all.py's own inventory
    gate refuses the run)."""

    SCRIPT = os.path.join(HERE, "..", "scripts", "migrate_install.py")

    def _env(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for k in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERME_CONFIG",
                  "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID"):
            env.pop(k, None)
        return env

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.SCRIPT] + list(args),
            env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", timeout=300)

    def _stranded_home(self, tmp):
        home = os.path.join(tmp, "home")
        skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
        os.makedirs(os.path.join(skill_dir, "hooks"))
        with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                     "w", encoding="utf-8") as fh:
            json.dump({"hooks": {}}, fh)
        with io.open(os.path.join(skill_dir, "marker.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("stray file from the stranded copy\n")
        return home, skill_dir

    # -- detect(): in-process, no subprocess needed ----------------------

    def test_detect_stranded_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = self._stranded_home(tmp)
            info = migrate_install.detect(home)
            self.assertTrue(info["has_skill_dir"])
            self.assertIsNone(info["settings_error"])
            self.assertTrue(info["stranded"], info)
            self.assertEqual(info["skill_dir"], skill_dir)

    def test_detect_clean_home_has_nothing_to_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            info = migrate_install.detect(home)
            self.assertFalse(info["has_skill_dir"])
            self.assertFalse(info["stranded"])

    def test_detect_working_clone_via_loader_hooks_json_is_not_stranded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            os.makedirs(os.path.join(skill_dir, "hooks"))
            with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                         "w", encoding="utf-8") as fh:
                fh.write('{"hooks": {"PreToolUse": [{"hooks": [{'
                        '"command": "python3 %s/tools/bm_fence_hook.py"'
                        '}]}]}}' % skill_dir)
            info = migrate_install.detect(home)
            self.assertTrue(info["has_skill_dir"])
            self.assertFalse(info["stranded"], info)

    def test_detect_unreadable_settings_is_no_data_never_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _skill_dir = self._stranded_home(tmp)
            settings_path = os.path.join(home, ".claude", "settings.json")
            with io.open(settings_path, "w", encoding="utf-8") as fh:
                fh.write("{ not valid json")
            info = migrate_install.detect(home)
            self.assertIsNotNone(info["settings_error"])
            self.assertIsNone(
                info["stranded"],
                "an unreadable settings.json must never be guessed past "
                "as stranded=True or stranded=False")

    # -- the dry-run plan, driven as the real CLI -------------------------

    def test_dry_run_plan_names_remove_install_and_settings_lines(self):
        """REPRODUCED pre-fix: this file did not exist at all, so there
        was no scriptable path from "doctor says install_shape FAILED"
        to a repair; the only remedy doctor could name was prose
        ("Run: python3 scripts/install.py --upgrade"), which still left
        the stranded copy's own stray files in place. Default (no
        --apply): prints REMOVE, INSTALL and the settings-line preview,
        and changes nothing on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = self._stranded_home(tmp)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("REMOVE:", r.stdout)
            self.assertIn("INSTALL:", r.stdout)
            self.assertIn("SETTINGS LINES", r.stdout)
            self.assertIn("[dry-run] Nothing was changed", r.stdout)
            # A dry run is a dry run: the stranded copy is untouched.
            self.assertTrue(os.path.isfile(
                os.path.join(skill_dir, "marker.txt")))

    def test_dry_run_on_a_clean_home_says_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("NOTHING TO DO", r.stdout)
            self.assertNotIn("REMOVE:", r.stdout)

    def test_apply_without_the_confirmation_flag_is_refused(self):
        """The founder gate: --apply alone must never be enough, because
        this rewires the hook wiring every future session under --home
        loads. Refused, with the reason stated, and nothing changed."""
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = self._stranded_home(tmp)
            r = self._run("--home", home, "--apply")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("REFUSED", r.stdout + r.stderr)
            self.assertIn("--i-understand-this-changes-every-session",
                          r.stdout + r.stderr)
            self.assertTrue(os.path.isfile(
                os.path.join(skill_dir, "marker.txt")),
                "a refused apply must change nothing on disk")

    def test_apply_with_confirmation_repairs_the_fixture_home(self):
        """The other half: both flags together actually perform the
        swap, against the fixture ONLY. Proves the live-swap path works
        without ever touching a real machine: the stranded marker file
        is gone afterward and a fresh, correctly wired install stands in
        its place."""
        with tempfile.TemporaryDirectory() as tmp:
            home, skill_dir = self._stranded_home(tmp)
            r = self._run("--home", home, "--apply",
                          "--i-understand-this-changes-every-session")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DONE", r.stdout)
            self.assertFalse(
                os.path.isfile(os.path.join(skill_dir, "marker.txt")),
                "the stranded copy's own stray file must be gone after "
                "a real apply")
            self.assertTrue(
                os.path.isfile(os.path.join(skill_dir, "tools",
                                            "bm_fence_hook.py")),
                "a real install must have landed the fence hook")
            info = migrate_install.detect(home)
            self.assertFalse(info["stranded"], info)

    def test_apply_with_confirmation_repairs_the_fixture_home_with_venv_embed_present(
            self):
        """REPRODUCED pre-fix, from a real incident (docs/plan): the vault
        lane's local embedding venv (tools/bm_embed_bge.py) lands at
        migrate_install.ROOT/.venv-embed, which is this project's OWN real
        checkout, not a fixture, because that is where the real machine
        state that caused the incident actually sat. A venv is symlinks by
        construction, and scripts/install.py's source walk (which this
        --apply path calls through) refuses any symlink it meets, so the
        exact test above (test_apply_with_confirmation_repairs_the_fixture_home)
        went red on any machine that had run the embed tooling.

        This test plants a REAL venv (python3 -m venv, not a stand-in
        symlink) at the real ROOT for the duration of the run only, and
        removes it in a finally block whatever happens, so no state
        survives this test either way. If a .venv-embed already exists at
        ROOT (a developer's own machine that really ran the embed
        tooling), this test SKIPs rather than clobbering it: the
        never-lose-work rule outranks this test's convenience."""
        real_venv = os.path.join(migrate_install.ROOT, ".venv-embed")
        if os.path.exists(real_venv):
            self.skipTest(
                "%s already exists on this machine; refusing to touch "
                "real machine state" % real_venv)
        r = subprocess.run(
            [sys.executable, "-m", "venv", real_venv],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)
        if r.returncode != 0:
            self.skipTest("python3 -m venv could not build the fixture: %s"
                          % (r.stdout + r.stderr))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home, skill_dir = self._stranded_home(tmp)
                r = self._run("--home", home, "--apply",
                              "--i-understand-this-changes-every-session")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("is a symlink", r.stdout + r.stderr)
                self.assertIn("DONE", r.stdout)
                self.assertFalse(
                    os.path.isfile(os.path.join(skill_dir, "marker.txt")),
                    "the stranded copy's own stray file must be gone "
                    "after a real apply")
                self.assertTrue(
                    os.path.isfile(os.path.join(skill_dir, "tools",
                                                "bm_fence_hook.py")),
                    "a real install must have landed the fence hook")
                self.assertFalse(
                    os.path.exists(os.path.join(skill_dir, ".venv-embed")),
                    "the reinstalled copy must not carry the source "
                    "tree's own venv")
        finally:
            shutil.rmtree(real_venv, ignore_errors=True)

    def test_wired_but_schema_behind_hook_is_not_nothing_to_do(self):
        """REPRODUCED pre-fix (M18, docs/plan/QUEUE.json, family
        evidence-integrity): build_plan asked only whether a fence hook
        was WIRED, never whether that wired copy could reach a verdict
        against this project's real schema. Measured on 2026-08-20: an
        installed clone's own bm_store.py was hundreds of commits stale
        while wiring was intact, and the dry run still printed NOTHING
        TO DO. This fixture builds a REAL, working copy of tools/ (the
        same dependency-closure technique
        TestM17FenceLivenessAgainstRealStore's own
        _old_install_one_schema_behind uses), its own bm_store.py
        patched down by exactly one schema, wired via skill_dir's own
        loader hooks.json (the same shape
        test_detect_working_clone_via_loader_hooks_json_is_not_stranded
        above uses to be "not stranded").

        REWRITTEN for M26 (docs/plan/QUEUE.json, family required-proof):
        this fixture's copy is a REAL, RUNNABLE bm_store.py, not a stub,
        so migrate_install.py's own real probe (_real_schema_probe) now
        actually runs it as a subprocess against a real, current-schema
        store and gets a genuine schema-ahead refusal back; this test no
        longer merely trusts a source-constant comparison to predict
        that outcome, it drives the process that produces it."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            tools_dir = os.path.join(skill_dir, "tools")
            os.makedirs(tools_dir)
            for name in sorted(os.listdir(HERE)):
                if name.endswith(".py") and not name.startswith("test_"):
                    shutil.copy2(os.path.join(HERE, name),
                                os.path.join(tools_dir, name))
            store_copy = os.path.join(tools_dir, "bm_store.py")
            with io.open(store_copy, encoding="utf-8") as fh:
                src = fh.read()
            old_line = "SCHEMA_VERSION = %d" % bs.SCHEMA_VERSION
            new_line = "SCHEMA_VERSION = %d" % (bs.SCHEMA_VERSION - 1)
            self.assertIn(old_line, src,
                          "fixture assumption broke: tools/bm_store.py no "
                          "longer defines %r verbatim" % old_line)
            with io.open(store_copy, "w", encoding="utf-8") as fh:
                fh.write(src.replace(old_line, new_line, 1))
            os.makedirs(os.path.join(skill_dir, "hooks"))
            with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                         "w", encoding="utf-8") as fh:
                fh.write('{"hooks": {"PreToolUse": [{"hooks": [{'
                        '"command": "python3 %s/bm_fence_hook.py"'
                        '}]}]}}' % tools_dir)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(
                "NOTHING TO DO", r.stdout,
                "a wired hook whose own bm_store.py is one schema behind "
                "this project's cannot reach a verdict against this "
                "project's real store; reporting NOTHING TO DO hides "
                "exactly the gap M18 exists to close. Output:\n%s"
                % r.stdout)
            self.assertIn(str(bs.SCHEMA_VERSION - 1), r.stdout,
                         "the plan must name the wired copy's own "
                         "(behind) schema number. Output:\n%s" % r.stdout)
            self.assertIn(str(bs.SCHEMA_VERSION), r.stdout,
                         "the plan must name this project's own "
                         "(current) schema number. Output:\n%s" % r.stdout)
            self.assertIn(
                "SCHEMA GAP", r.stdout,
                "the verdict must be a real SCHEMA GAP, not a NO-DATA "
                "shrug, for a real store the wired copy genuinely "
                "cannot open. Output:\n%s" % r.stdout)

    def test_wired_copy_that_is_this_projects_own_tree_is_not_nothing_to_do(self):
        """REPRODUCED pre-fix (M18 follow-up, orchestrator review): ROOT
        in migrate_install.py is wherever the running script itself
        lives, so running the INSTALLED copy's own scripts/
        migrate_install.py makes ROOT and skill_dir the same directory.
        A symlinked dev install collapses them the same way (an install
        clone is exactly the kind of thing that gets symlinked). The
        naive version comparison then reads one file, compares it to
        itself, finds wired_version == current_version by construction,
        and returns None: build_plan falls through to NOTHING TO DO
        about a dimension it never actually looked at, the identical
        M17/M18 defect reappearing inside the M18 fix itself.

        STILL VALID under M26 (docs/plan/QUEUE.json, family
        required-proof): this self-path collapse stays a cheap
        disqualifier the real probe never even runs for (a copy cannot
        be probed against itself), so this fixture and its assertions
        are unchanged; only the underlying implementation now reaches
        this same NO-DATA line by a different, still-cheap route."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            os.makedirs(os.path.join(home, ".claude", "skills"))
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            # skill_dir IS this project's own tree, reached through a
            # symlink, collapsing ROOT and skill_dir to the same real
            # path the same way running the installed copy's own script
            # would.
            os.symlink(migrate_install.ROOT, skill_dir)
            fence = os.path.join(skill_dir, "tools", "bm_fence_hook.py")
            settings = os.path.join(home, ".claude", "settings.json")
            with io.open(settings, "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [{
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                    "hooks": [{"type": "command",
                              "command": "python3 " + fence,
                              "timeout": 10}]}]}}, fh)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(
                "NOTHING TO DO", r.stdout,
                "comparing a copy's schema against itself is not a "
                "check; it must not be reported as NOTHING TO DO. "
                "Output:\n%s" % r.stdout)
            self.assertIn(
                "NO-DATA", r.stdout,
                "the self-comparison must say plainly it could not "
                "tell, not stay silent about it. Output:\n%s" % r.stdout)

    def test_agreeing_constants_but_unopenable_real_store_is_not_nothing_to_do(self):
        """THE MUTATION PROOF M26 asks for by name (docs/plan/QUEUE.json,
        family required-proof, done_check: "fails when the code is
        changed to compare source constants only"). The wired copy's own
        SCHEMA_VERSION is left EXACTLY as this project's own (a plain
        dependency-closure copy, untouched), so a SOURCE-CONSTANT
        COMPARISON sees wired_version == current_version and returns
        None: NOTHING TO DO. But the copy is corrupted a different way
        that has nothing to do with schema (an import of a module that
        does not exist, inserted before anything else in the file runs),
        so the wired copy genuinely cannot reach a verdict against a
        real store: it crashes before it ever gets to open one. Only a
        REAL PROBE that actually runs the wired copy's own code can see
        that; a comparison of two integers that agree cannot.

        CALIBRATION (done-check, run by hand, not by this test): with
        scripts/migrate_install.py's _schema_gap_message reduced to the
        old `if wired_version >= current_version: return None` shape,
        this test fails (constants agree, so NOTHING TO DO). Restored to
        the real probe, it passes."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            tools_dir = os.path.join(skill_dir, "tools")
            os.makedirs(tools_dir)
            for name in sorted(os.listdir(HERE)):
                if name.endswith(".py") and not name.startswith("test_"):
                    shutil.copy2(os.path.join(HERE, name),
                                os.path.join(tools_dir, name))
            store_copy = os.path.join(tools_dir, "bm_store.py")
            with io.open(store_copy, encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn(
                "SCHEMA_VERSION = %d" % bs.SCHEMA_VERSION, src,
                "fixture assumption broke: tools/bm_store.py no longer "
                "defines %r verbatim" % ("SCHEMA_VERSION = %d" % bs.SCHEMA_VERSION,))
            # SCHEMA_VERSION is left untouched, agreeing exactly with
            # this project's own; the corruption is an import that fails
            # at module load, before main() or any command runs, so
            # running this copy always crashes for a reason a schema
            # comparison could never see.
            with io.open(store_copy, "w", encoding="utf-8") as fh:
                fh.write(
                    "import bm_store_m26_mutation_proof_missing_module\n"
                    + src)
            os.makedirs(os.path.join(skill_dir, "hooks"))
            with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                         "w", encoding="utf-8") as fh:
                fh.write('{"hooks": {"PreToolUse": [{"hooks": [{'
                        '"command": "python3 %s/bm_fence_hook.py"'
                        '}]}]}}' % tools_dir)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(
                "NOTHING TO DO", r.stdout,
                "a wired copy whose SCHEMA_VERSION AGREES with this "
                "project's own, but which cannot even be imported, "
                "still cannot reach a verdict against a real store; a "
                "source-constant comparison alone cannot see that, only "
                "a real probe can. Output:\n%s" % r.stdout)
            self.assertIn(
                "NO-DATA", r.stdout,
                "a real probe failure for a non-schema reason must say "
                "plainly it could not tell, not stay silent. Output:\n%s"
                % r.stdout)


    # -- M22: the version reported must be the one that would be USED --

    def _plant_store(self, tools_dir, body):
        """Write a throwaway tools/bm_store.py carrying `body` and
        return its path. Never imported by the code under test: the
        whole point of M22 is that a foreign copy must be answered
        ABOUT without being executed."""
        os.makedirs(tools_dir, exist_ok=True)
        path = os.path.join(tools_dir, "bm_store.py")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_schema_version_of_refuses_a_shadowed_constant(self):
        """REPRODUCED pre-fix (M22, docs/plan/QUEUE.json, family
        evidence-integrity, cross-family debate 2026-08-21 finding 10):
        _schema_version_of returned the integer after the FIRST
        "SCHEMA_VERSION = " line it saw, so a module that assigns the
        name twice certifies a version the module would never use. The
        text read answers about the literal; the question is about the
        effective value. Where the effective value cannot be known
        without executing the file, and executing a stale or foreign
        copy is exactly what this function refuses to do, the only
        honest answer is NO-DATA."""
        with tempfile.TemporaryDirectory() as tmp:
            planted = self._plant_store(os.path.join(tmp, "tools"),
                                        "SCHEMA_VERSION = 3\n"
                                        "SCHEMA_VERSION = 9\n")
            got = migrate_install._schema_version_of(planted)
            self.assertIsNone(
                got,
                "the file assigns SCHEMA_VERSION twice at module level, "
                "so the value it would actually use is 9 and the first "
                "literal is 3. Reporting %r certifies a version that is "
                "not real; the answer must be None, which the caller "
                "renders as NO-DATA." % (got,))

    def test_schema_version_of_refuses_a_conditional_constant(self):
        """REPRODUCED pre-fix (M22): the old reader stripped each line
        before matching, so an assignment nested inside an `if` matched
        exactly like a top-level one and the FIRST branch's literal was
        returned regardless of which branch would run. Which branch runs
        is a runtime fact, so the answer is NO-DATA."""
        with tempfile.TemporaryDirectory() as tmp:
            planted = self._plant_store(
                os.path.join(tmp, "tools"),
                "import os\n"
                "if os.environ.get('BM_STORE_NEXT'):\n"
                "    SCHEMA_VERSION = 99\n"
                "else:\n"
                "    SCHEMA_VERSION = 3\n")
            got = migrate_install._schema_version_of(planted)
            self.assertIsNone(
                got,
                "the effective SCHEMA_VERSION depends on the "
                "environment at import time, so no static reader can "
                "name it. Reporting %r is a guess dressed as a "
                "measurement." % (got,))

    def test_schema_version_of_still_reads_the_ordinary_shape(self):
        """The fix must not turn every real file into NO-DATA: this
        project's own tools/bm_store.py has one plain top-level literal
        and must still be read, or M18's whole comparison degrades to
        NO-DATA everywhere and stops catching stale installs."""
        with tempfile.TemporaryDirectory() as tmp:
            planted = self._plant_store(os.path.join(tmp, "tools"),
                                        '"""docstring."""\n'
                                        "import os\n"
                                        "SCHEMA_VERSION = 7\n"
                                        "def f():\n"
                                        "    return SCHEMA_VERSION\n")
            self.assertEqual(migrate_install._schema_version_of(planted), 7)
        real = os.path.join(migrate_install.ROOT, "tools", "bm_store.py")
        self.assertEqual(
            migrate_install._schema_version_of(real), bs.SCHEMA_VERSION,
            "this project's own store file must still be readable by "
            "the static reader; if it is not, the M18 comparison has "
            "nothing to compare against.")

    def test_shadowed_constant_in_a_wired_copy_is_not_nothing_to_do(self):
        """REPRODUCED pre-fix (M22 end to end): the failure that makes
        this MAJOR rather than cosmetic. A wired copy whose FIRST
        SCHEMA_VERSION literal equals this project's own, but which
        reassigns the name afterwards, passed the M18 comparison as
        current and the dry run printed NOTHING TO DO. The install is
        exactly as unable to reach a verdict as the plainly stale one
        M18 already catches, and it hid behind a literal."""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            skill_dir = os.path.join(home, ".claude", "skills", "brothermode")
            tools_dir = os.path.join(skill_dir, "tools")
            os.makedirs(tools_dir)
            for name in sorted(os.listdir(HERE)):
                if name.endswith(".py") and not name.startswith("test_"):
                    shutil.copy2(os.path.join(HERE, name),
                                 os.path.join(tools_dir, name))
            store_copy = os.path.join(tools_dir, "bm_store.py")
            with io.open(store_copy, encoding="utf-8") as fh:
                src = fh.read()
            old_line = "SCHEMA_VERSION = %d" % bs.SCHEMA_VERSION
            self.assertIn(old_line, src,
                          "fixture assumption broke: tools/bm_store.py no "
                          "longer defines %r verbatim" % old_line)
            # The literal a text reader sees first stays current; the
            # value the module would really use is one schema behind.
            src = src.replace(
                old_line,
                "%s\nSCHEMA_VERSION = %d" % (old_line, bs.SCHEMA_VERSION - 1),
                1)
            with io.open(store_copy, "w", encoding="utf-8") as fh:
                fh.write(src)
            os.makedirs(os.path.join(skill_dir, "hooks"))
            with io.open(os.path.join(skill_dir, "hooks", "hooks.json"),
                         "w", encoding="utf-8") as fh:
                fh.write('{"hooks": {"PreToolUse": [{"hooks": [{'
                         '"command": "python3 %s/bm_fence_hook.py"'
                         '}]}]}}' % tools_dir)
            r = self._run("--home", home)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(
                "NOTHING TO DO", r.stdout,
                "the wired copy reassigns SCHEMA_VERSION after the "
                "literal a text reader sees, so its effective schema is "
                "unknown to any static reader. NOTHING TO DO certifies "
                "a version that is not real. Output:\n%s" % r.stdout)
            self.assertIn(
                "NO-DATA", r.stdout,
                "an unreadable effective version must be said plainly, "
                "not guessed from the first literal. Output:\n%s"
                % r.stdout)


class TestLoop11McpCopyFirstIsAutomated(unittest.TestCase):
    """LOOP 11 workstream C. The MCP server's copy-first design (snapshot the
    project, read the COPY, delete the copy) was argued in a long docstring
    and checked by hand. These are the committed tests: real store bytes and
    sidecars unchanged, no snapshot left behind, and a removal that fails is
    REPORTED rather than swallowed."""

    MCP_SERVER_PATH = os.path.join(HERE, "..", "mcp", "bm_mcp_server.py")
    SNAPSHOT_PREFIX = "bm_mcp_readonly_snapshot_"

    def _seed(self, root):
        store = bs.Store(root)
        try:
            rec = store.claim("payments", "persistent", "an objective",
                              ["api/pay.py"], session_id="s1")
            store.decide(rec.lifecycle_uuid, rec.version, "topic", "a decision")
        finally:
            store.close()

    def _fingerprint(self, root):
        """Every file under `root`: relative path -> (size, sha256, mtime_ns).
        Content AND metadata, because a read that rewrote a file with
        identical bytes would still be a write."""
        import hashlib
        out = {}
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                st = os.lstat(full)
                with io.open(full, "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
                out[rel] = (st.st_size, digest, st.st_mtime_ns)
        return out

    def _drive(self, root, tool):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test_bm", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": tool, "arguments": {"project_root": root}}},
        ]
        env = dict(os.environ)
        env.pop("BROTHERMODE_ROOT", None)
        p = subprocess.run(
            [sys.executable, self.MCP_SERVER_PATH],
            input="".join(json.dumps(m) + "\n" for m in msgs),
            capture_output=True, text=True, env=env)
        for line in p.stdout.splitlines():
            obj = json.loads(line)
            if obj.get("id") == 3:
                result = obj.get("result") or {}
                return result.get("isError"), "".join(
                    c.get("text", "") for c in result.get("content", []))
        self.fail("server sent no tools/call result; stderr: %s" % p.stderr)

    def test_every_read_only_tool_leaves_the_real_store_byte_identical(self):
        with tempfile.TemporaryDirectory() as base:
            root = os.path.realpath(os.path.join(base, "proj"))
            os.makedirs(root)
            self._seed(root)
            before = self._fingerprint(root)
            self.assertTrue(before, "sanity: the project must have files to compare")
            for tool in ("bm_status", "bm_active_work", "bm_fences", "bm_decisions"):
                is_error, text = self._drive(root, tool)
                self.assertFalse(is_error, "%s was refused: %s" % (tool, text))
            after = self._fingerprint(root)
            self.assertEqual(before, after,
                              "an MCP read changed the real project on disk")
            # Sidecars: the set next to the REAL store must be exactly what
            # it was before the reads. (The seeding writer may legitimately
            # leave a -wal behind; what must never happen is an MCP read
            # ADDING or touching one, which the fingerprint above also
            # covers by content and mtime.)
            for sidecar in ("-wal", "-shm"):
                self.assertEqual(
                    os.path.exists(bs.store_path(root) + sidecar),
                    sidecar in "".join(before),
                    "an MCP read changed the %s sidecar next to the REAL "
                    "store; copy-first exists precisely so sqlite only ever "
                    "touches the throwaway copy" % sidecar)

    def test_no_snapshot_directory_survives_a_tool_call(self):
        with tempfile.TemporaryDirectory() as base:
            root = os.path.realpath(os.path.join(base, "proj"))
            os.makedirs(root)
            self._seed(root)
            tmp = tempfile.gettempdir()
            before = set(glob.glob(os.path.join(tmp, self.SNAPSHOT_PREFIX + "*")))
            self._drive(root, "bm_active_work")
            after = set(glob.glob(os.path.join(tmp, self.SNAPSHOT_PREFIX + "*")))
            self.assertEqual(sorted(after - before), [],
                              "a snapshot (a COMPLETE COPY of a project's store) "
                              "was left on disk after the tool call returned")

    def test_a_snapshot_that_cannot_be_removed_is_reported_not_swallowed(self):
        """The cleanup-FAILURE path, which no hand test ever exercises: the
        removal is forced to fail and the operator must be told, by name, that
        a copy of a store is still on disk."""
        mcp_mod = TestGate3OutputFunnelCoversTheMcpServerToo._load_mcp_server()
        leftover = []
        real_rmtree = shutil.rmtree

        def exploding_rmtree(path, *a, **kw):
            leftover.append(path)
            raise OSError(13, "simulated permission denied")

        with tempfile.TemporaryDirectory() as base:
            root = os.path.realpath(os.path.join(base, "proj"))
            os.makedirs(root)
            self._seed(root)
            err = io.StringIO()
            mcp_mod.shutil.rmtree = exploding_rmtree
            try:
                with contextlib.redirect_stderr(err):
                    with mcp_mod._snapshot_for_reading(root) as snap:
                        self.assertTrue(os.path.isdir(snap))
            finally:
                mcp_mod.shutil.rmtree = real_rmtree
                for path in leftover:
                    real_rmtree(path, ignore_errors=True)
            message = err.getvalue()
            self.assertIn("FAILED to remove", message)
            self.assertIn("still on disk", message)
            self.assertTrue(leftover, "sanity: a snapshot must have been created")
            self.assertIn(leftover[0], message,
                          "the report must NAME the directory an operator has to "
                          "delete, or it is not actionable")

    def test_calibrated_reinjecting_ignore_errors_hides_the_failed_cleanup(self):
        """CALIBRATION for the test above: with the pre-FINDING-3c behavior
        (rmtree(..., ignore_errors=True)) the same forced failure produces no
        report at all, which is the defect."""
        with tempfile.TemporaryDirectory() as base:
            snapshot_root = os.path.join(base, "snap")
            os.makedirs(snapshot_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                shutil.rmtree(snapshot_root + "-does-not-exist", ignore_errors=True)
            self.assertEqual(err.getvalue(), "",
                              "REINJECTION CHECK: ignore_errors=True really is "
                              "silent, which is why the shipped code cannot use it")

    def test_the_snapshot_copy_never_dereferences_a_link_on_any_platform(self):
        """Structural, so it holds on the platforms this suite cannot run on.
        copytree's DEFAULT (symlinks=False) is what let another project's
        store be dereferenced into a snapshot (FINDING 3); symlinks=True is
        the second of the two guards and must stay explicit in the source.
        HONEST SCOPE: this asserts the call shape, not Windows behavior. On
        Windows, junctions and reparse points are NOT proven by this suite or
        by CI, and nothing in this project claims they are."""
        with io.open(self.MCP_SERVER_PATH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("symlinks=True", src)
        self.assertIn("shutil.rmtree(snapshot_root)\n", src,
                      "the snapshot removal must stay a PLAIN rmtree; adding "
                      "ignore_errors=True would reopen FINDING 3c (a failed "
                      "cleanup leaving a copy of a store on disk, silently)")


class TestLoop11WindowsClaimsMatchEvidence(unittest.TestCase):
    """LOOP 11 workstream B. Owner-only file modes are a POSIX guarantee. On
    Windows, `os.chmod` cannot express an ACL, and this project ships stdlib
    only, with no subprocess in the shipping tools, so it CANNOT configure one
    (icacls would be a subprocess; pywin32 would be a dependency). The honest
    move is therefore to claim nothing there and to keep every published
    owner-only sentence qualified. That is what this test enforces, since
    documentation is where over-claiming actually reaches a reader.

    NOT PROVEN HERE, and not proven anywhere in this project: any behavior on
    a real Windows host. This suite runs on macOS and Linux."""

    QUALIFIERS = ("posix", "windows", "best-effort", "best effort")

    def _docs(self):
        root = os.path.join(HERE, "..")
        return [os.path.join(root, "README.md"), os.path.join(root, "SECURITY.md")]

    def test_every_published_owner_only_claim_names_its_platform_scope(self):
        offenders = []
        for path in self._docs():
            if not os.path.isfile(path):
                continue
            with io.open(path, encoding="utf-8") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if "owner-only" not in line.lower():
                    continue
                window = " ".join(lines[max(0, i - 2):i + 3]).lower()
                if not any(q in window for q in self.QUALIFIERS):
                    offenders.append("%s:%d" % (os.path.basename(path), i + 1))
        self.assertEqual(offenders, [],
                          "unqualified owner-only claim(s): on Windows this "
                          "project cannot configure an ACL with the standard "
                          "library and no subprocess, so every such claim must "
                          "name POSIX or say best-effort: %s" % offenders)

    def test_the_chmod_helper_promises_nothing_the_platform_may_not_keep(self):
        # Behavioral, not a doc scan: a chmod that the platform ignores must
        # never raise or be escalated, on any platform this runs on.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f")
            with io.open(path, "w") as f:
                f.write("x")
            bs._chmod_best_effort(path, 0o600)
            bs._chmod_best_effort(os.path.join(d, "does-not-exist"), 0o600)
            if sys.platform != "win32":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            else:  # pragma: no cover - not exercised by this suite
                self.skipTest("owner-only modes are a POSIX claim only")

    def test_no_shipping_tool_shells_out_to_configure_an_acl(self):
        """The reason the Windows claim stays narrow, asserted structurally:
        an ACL adapter would need icacls (a subprocess) or pywin32 (a
        dependency), and both are outside this project's constraints. If one
        ever lands, this test fails and the claim gets revisited deliberately."""
        for name in ("bm_store.py", "bm_threads.py", "bm_telemetry.py",
                     "bm_learn.py", "bm_learning.py", "bm_score.py"):
            with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
                src = f.read()
            for banned in ("icacls", "win32security", "pywin32"):
                self.assertNotIn(banned, src,
                                  "%s reaches for %s: a Windows ACL adapter is a "
                                  "deliberate decision with its own review, not a "
                                  "quiet import" % (name, banned))


class TestLoop11SecretScanHardening(unittest.TestCase):
    """LOOP 11 workstream D. The word-boundary class was fixed once already
    (LOOP 12 in bm_telemetry.py: Python counts '_' as a word character, so
    every \\b-anchored vendor pattern missed OPENAI_KEY_sk-live_...). These
    are the committed cases for prefixes, suffixes, underscores, punctuation
    and long input, so the next edit to SECRET_PATTERNS cannot quietly undo
    it, plus a ceiling on redaction time."""

    def _redact(self, text):
        return bm.redact(text)[0]

    def test_vendor_keys_are_caught_behind_any_separator_or_prefix(self):
        secrets = [
            "sk-live_abcdefghijklmnopqrstuvwx",
            "ghp_abcdefghijklmnopqrstuvwxyz012345",
            "AKIAIOSFODNN7EXAMPLE",
            "xoxb-1234567890-abcdefghijkl",
        ]
        # DOCUMENTED BOUNDARY: letters and digits BIND a token, separators do
        # not (bm_telemetry._BEFORE/_AFTER). So every separator-ish prefix
        # below must be caught, and "123sk-live_..." deliberately is NOT: see
        # test_the_alphanumeric_boundary_is_deliberate_and_is_a_known_limit.
        prefixes = ["", "OPENAI_KEY_", "MY-KEY=", "key:", "(", "[", "'", "\"",
                    "token is ", "prefix_", "-", "/", "\n"]
        suffixes = ["", ")", "]", ".", ",", "'", "\"", ";", "\n"]
        for secret in secrets:
            for pre in prefixes:
                for suf in suffixes:
                    line = "%s%s%s" % (pre, secret, suf)
                    self.assertNotIn(secret, self._redact(line),
                                     "unredacted in %r" % line)

    def test_the_alphanumeric_boundary_is_deliberate_and_is_a_known_limit(self):
        """A secret glued directly to LETTERS OR DIGITS with no separator
        ("123sk-live_...") is not matched, by design: the alternative is a
        pattern that fires inside hashes and identifiers. Asserted so the
        limit is a decision on record rather than a surprise, and written up
        in docs/KNOWN-LIMITS.md."""
        glued = "123sk-live_abcdefghijklmnopqrstuvwx"
        self.assertEqual(self._redact(glued), glued)
        self.assertNotIn("sk-live_abcdefghijklmnopqrstuvwx",
                          self._redact("_" + glued[3:]))

    def test_a_secret_at_the_end_of_a_long_line_is_still_caught(self):
        line = ("ordinary prose about the project. " * 2000) + \
               " AKIAIOSFODNN7EXAMPLE"
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", self._redact(line))

    def test_ordinary_words_are_not_mistaken_for_keys(self):
        for benign in ("task-oriented", "risk-free", "whisk-y", "and/or"):
            self.assertEqual(self._redact(benign), benign)

    def _large_blob(self, units):
        """The workstream D worst case, sized in units so two of them can be
        compared. One unit is about 630 characters: a dense key-shaped
        alphanumeric run, a real secret in the middle, then ordinary prose.

        FIXED 2026-08-10, and read this before changing the filler. The run
        used to be "A1b2C3d4E5f6G7h8", pure letters and digits, and the
        key=value pattern opens with the boundary lookbehind (?<![A-Za-z0-9]),
        so only the first offset of that run could ever start a match. On
        that text the quadratic this test exists to catch is UNREACHABLE:
        reinjecting the pre-Loop-12 unbounded pattern leaves the cost linear,
        exactly as tools/test_bm.py's TestLoop12 class records for its own
        letter-run probe. The "_" is the one character excluded from that
        lookbehind while still being inside the [A-Za-z0-9_]* run that
        follows, so one underscore per five characters gives the defect its
        n starting positions back and makes this probe able to fail. Proven
        by test_calibrated_the_unbounded_pattern_blows_up_this_blob below."""
        return (("A1b2_C3d4_E5f6_G7h8_" * (units * 16))
                + " password=hunter2 "
                + ("some ordinary sentence about the work. " * (units * 8)))

    def _time(self, text, samples=5):
        """Minimum of five samples, the same estimator and for the same
        reason as TestLoop12RedactionIsLinearInInputSize._time above (C-11,
        2026-08-04): noise can only ADD latency to a sample, never remove it,
        so the minimum converges on the true unloaded cost."""
        import time
        best = None
        for _ in range(samples):
            start = time.time()
            bm.redact(text)
            elapsed = time.time() - start
            if best is None or elapsed < best:
                best = elapsed
        return best

    def test_redaction_of_a_large_input_stays_linear(self):
        """PERFORMANCE SHAPE. The key=value pattern was quadratic once (an
        unbounded prefix retried at every offset of a long alphanumeric run):
        one 20 KB correction stalled an import for 75 seconds.

        FIXED 2026-08-10. This test was named
        test_redaction_of_a_large_input_stays_under_the_ceiling and asserted
        `elapsed < 10.0` on one wall-clock sample. A ten-second budget is a
        fact about the MACHINE, not about the pattern: the identical shape in
        this file's TestLoop12 class failed at 1023s on a five-session laptop
        with the code under test unchanged, and its sibling comment already
        says in so many words that a wall-clock budget is a blunt instrument.
        Worse, the ceiling was also loose enough that the defect could sit
        under it, which is the second half of the same 2026-07-31 finding.

        So the same fix lands here: assert the SHAPE. Four times the input
        must not take anything like sixteen times the work, both timings are
        taken under whatever load is present so contention cancels in the
        ratio, and each is the minimum of five samples so one stall cannot
        land on one size alone. The functional half, that a secret buried in
        a large input is still redacted, is kept and is now checked at BOTH
        sizes rather than one. Measured 2026-08-10: 40 KB took 0.0297s and
        162 KB took 0.1233s, a 4.15x ratio where linear is about 4x. The
        large leg is bigger than the 142 KB the old ceiling test used, so no
        input coverage is lost."""
        small_blob, large_blob = self._large_blob(64), self._large_blob(256)
        for blob in (small_blob, large_blob):
            self.assertNotIn("hunter2", self._redact(blob),
                             "a secret in a %d KB input was not redacted"
                             % (len(blob) // 1024))
        small = max(self._time(small_blob), 0.001)
        large = self._time(large_blob)
        self.assertLess(large / small, 8.0,
                        "redact() scaling looks superlinear on the workstream "
                        "D blob: %d KB took %.4fs, %d KB took %.4fs, a %.1fx "
                        "ratio where linear is about 4x and quadratic about "
                        "16x" % (len(small_blob) // 1024, small,
                                 len(large_blob) // 1024, large, large / small))

    def test_calibrated_the_unbounded_pattern_blows_up_this_blob(self):
        """CALIBRATION for the test above: a probe that cannot reach the
        defect measures nothing.

        Reinjects the pre-Loop-12 unbounded key=value pattern, the exact one
        whose bounded {0,40} prefix is the shipped fix, and the ratio must go
        superlinear on THIS blob shape. That is what proves the sibling test
        can still fail for the reason it exists, rather than passing for
        free.

        Sizes are 4 and 16 units (2.5 KB and 10 KB) rather than the sibling's
        64 and 256, to keep this test near two seconds instead of near two
        minutes: quadratic cost rises fast and the ratio is what is asserted,
        not the absolute time. Measured 2026-08-10, minimum of five samples
        per size: unbounded gave 0.0181s and 0.2464s, a 13.6x ratio, against
        the 4.15x the shipped bounded pattern gives on the same shape. The
        same reinjection on the OLD letters-only filler gave a linear ratio,
        which is why _large_blob now carries underscores."""
        unbounded = re.compile(
            bm._BEFORE + r"[A-Za-z0-9_]*(?:pass(?:word|wd|phrase)?"
            r"|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
            r"|credential)s?\s*(?:[:=]|\s+(?:is|was)\s+)\s*\S+", re.I)
        original = bm.SECRET_PATTERNS
        reinjected = list(original)
        reinjected[8] = unbounded
        bm.SECRET_PATTERNS = reinjected
        try:
            small = max(self._time(self._large_blob(4)), 0.001)
            large = self._time(self._large_blob(16))
        finally:
            bm.SECRET_PATTERNS = original
        self.assertGreaterEqual(
            large / small, 8.0,
            "REINJECTION CHECK: the pre-Loop-12 unbounded key=value pattern "
            "must still show a superlinear ratio on the workstream D blob, "
            "so that the test above is proven able to catch it. 2.5 KB took "
            "%.4fs, 10 KB took %.4fs, a %.1fx ratio where 13.6x was measured "
            "when this was written and anything near 4x means the "
            "calibration has been lost" % (small, large, large / small))
        # The shipped pattern must be back: a leaked monkeypatch would make
        # every later redaction test in this process pass or fail for reasons
        # unrelated to the code under test.
        self.assertIn("{0,40}", bm.SECRET_PATTERNS[8].pattern)


class TestP17PackagingManifestMatchesTheRepository(unittest.TestCase):
    """LOOP P17. pyproject.toml is a second, hand-maintained copy of two
    facts that already exist in the repository: which modules ship, and what
    version this is. Hand-maintained copies drift, and this one drifts
    SILENTLY: a new tool lands in tools/, nobody adds it to py-modules, and
    the wheel builds green while the installed command crashes on its first
    import for whoever installed from PyPI. The failure surfaces on a
    stranger's machine, never on ours.

    So the list is not allowed to be a wildcard and is not allowed to be
    stale. These tests are the reason the comment in pyproject.toml can
    claim the list is checked.

    No tomllib: this project's floor is Python 3.9 and tomllib arrived in
    3.11. The parsing below is deliberately narrow (it reads exactly the
    three constructs it needs, from a file we control) rather than a general
    TOML parser pretending to be one."""

    ROOT = os.path.dirname(HERE)
    PYPROJECT = os.path.join(ROOT, "pyproject.toml")

    def _text(self):
        self.assertTrue(os.path.isfile(self.PYPROJECT),
                        "pyproject.toml is missing: the packaging contract "
                        "cannot be checked and must not be assumed")
        return _read(self.PYPROJECT)

    def _py_modules(self):
        m = re.search(r"^py-modules\s*=\s*\[(.*?)\]", self._text(),
                      re.S | re.M)
        self.assertIsNotNone(m, "py-modules array not found in pyproject.toml")
        return set(re.findall(r'"([^"]+)"', m.group(1)))

    def _scripts(self):
        m = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)",
                      self._text(), re.S | re.M)
        self.assertIsNotNone(m, "[project.scripts] not found in pyproject.toml")
        out = {}
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, target = line.partition("=")
            out[name.strip()] = target.strip().strip('"')
        self.assertTrue(out, "[project.scripts] is empty")
        return out

    def _shipping_modules(self):
        """Every Python file in tools/ that is not a test. That is the set
        a user who installed from PyPI must actually receive."""
        names = set()
        for f in os.listdir(HERE):
            if f.endswith(".py") and not f.startswith("test_"):
                names.add(f[:-3])
        return names

    def test_every_shipping_tool_is_in_py_modules(self):
        missing = self._shipping_modules() - self._py_modules()
        self.assertEqual(
            set(), missing,
            "these tools ship in tools/ but pyproject.toml would not install "
            "them, so a pipx or pip install is missing them: %s"
            % sorted(missing))

    def test_py_modules_names_nothing_that_does_not_exist(self):
        extra = self._py_modules() - self._shipping_modules()
        self.assertEqual(
            set(), extra,
            "pyproject.toml names modules that are not shipping tools in "
            "tools/ (a deleted or renamed file, or a test swept in): %s"
            % sorted(extra))

    #: P17-fix. The py-modules contract above can only ever see *.py, because
    #: py-modules can only name modules. Anything else in tools/ is therefore
    #: invisible to it, and `include-package-data = false` drops it from both
    #: artifacts without a word. That is fine for a repo-only file and a
    #: silent hole for a runtime asset (a future registry .json, a data table,
    #: a template), which would install missing and fail on a stranger's
    #: machine exactly the way a missing module would.
    #:
    #: So every non-.py file in tools/ carries a decision here, with the
    #: reason. Repo-only files are listed below; files that must reach an
    #: installed user go in SHIPPING_ASSETS and must then actually be carried
    #: by the packaging. A new unclassified file fails this suite, which is
    #: the whole point: the decision gets made by a person, once, on purpose.
    REPO_ONLY_ASSETS = {
        # bm_sessionstart.sh's entry is GONE from this table, and its
        # absence is the 2026-08-17 Windows port rather than an oversight:
        # the file is bm_sessionstart.py now, so py-modules can see it and
        # the two tests above govern it like every other module. It was
        # only ever classified here because a .sh file is invisible to
        # py-modules, which can name modules and nothing else.
        "bm-embed-bge": (
            "a shell shim that execs an interpreter inside a .venv-embed "
            "virtual environment created beside the checkout. It resolves "
            "next to itself, so it is meaningful only where that environment "
            "was built by hand; an installed user has no such directory and "
            "would receive a shim that cannot run. The embedding module "
            "itself (bm_embed_bge) DOES ship, and is invoked directly."),
        "bm_embed.swift": (
            "Swift source for the optional on-device embedding path, "
            "compiled by hand where it is wanted. A Python distribution "
            "cannot build or run it, so shipping it would put a file in an "
            "installed tree that nothing there can use."),
        "write_sites.json": (
            "the reviewed inventory of write sites. A review artifact for "
            "this suite (test_no_unreviewed_write_sites), read by no "
            "shipping tool at runtime."),
        "WEEKLY-REVIEW.md": (
            "a human checklist. Documentation, not runtime input."),
        "toolkit_conflict_classes.json": (
            "the founder-editable OVERRIDE for the Toolkit's conflict "
            "classes and their severities. It stays behind on purpose "
            "(decision 34, 2026-08-12): the shipped defaults live in "
            "bm_toolkit.py as a module constant, so a packaged install "
            "always has working classes, and this file only overrides them "
            "where it exists. Carrying it as package data instead would "
            "mean a flat top-level module reaching for a data file that "
            "include-package-data = false drops, which is how a packaged "
            "toolkit would end up silently classless. Severity is a "
            "judgement about how one team works, so the override path is "
            "the point of the file, not the defaults."),
        "toolkit_routes.json": (
            "the founder-editable OVERRIDE for the Toolkit's routing "
            "table, the same decision-34 shape as its sibling above "
            "(TK4, 2026-08-15): the shipped default routes live in "
            "bm_toolkit.py as a module constant, byte-identical to this "
            "file, so a packaged install always has working routes, and "
            "this file only overrides them where it exists. Which "
            "capability a team routes a task class to is a judgement "
            "about how that team works, so the override path is the "
            "point of the file here too."),
    }

    #: name -> why it must reach an installed user. Empty today, on purpose:
    #: no shipping tool reads a non-.py file at runtime. The test below is
    #: what makes adding an entry here mean something.
    SHIPPING_ASSETS = {}

    def _non_module_assets(self):
        return set(f for f in os.listdir(HERE)
                   if os.path.isfile(os.path.join(HERE, f))
                   and not f.endswith(".py") and not f.startswith("."))

    def test_every_non_module_asset_in_tools_carries_a_shipping_decision(self):
        classified = set(self.REPO_ONLY_ASSETS) | set(self.SHIPPING_ASSETS)
        found = self._non_module_assets()
        self.assertEqual(
            set(), found - classified,
            "these non-.py files live in tools/ and nothing decides whether "
            "an installed user gets them; py-modules cannot see them and "
            "include-package-data = false drops them silently. Add each to "
            "REPO_ONLY_ASSETS (with the reason it stays behind) or to "
            "SHIPPING_ASSETS (and carry it in the packaging): %s"
            % sorted(found - classified))
        self.assertEqual(
            set(), classified - found,
            "these files are classified here but no longer exist in tools/, "
            "so the classification is stale: %s" % sorted(classified - found))

    def test_no_asset_is_classified_both_ways(self):
        both = set(self.REPO_ONLY_ASSETS) & set(self.SHIPPING_ASSETS)
        self.assertEqual(set(), both,
                         "classified as both repo-only and shipping: %s"
                         % sorted(both))

    def test_assets_declared_shipping_are_actually_carried_by_the_packaging(self):
        """Declaring an asset shipping is not the same as shipping it. With
        flat py-modules there is no package directory to attach data to, so
        carrying one takes an explicit construct (a data-files or
        package-data table, or a MANIFEST.in). This test refuses the state
        where the table says an asset ships and the build drops it."""
        manifest_in = os.path.join(self.ROOT, "MANIFEST.in")
        carried = self._text()
        if os.path.isfile(manifest_in):
            carried += _read(manifest_in)
        for name in sorted(self.SHIPPING_ASSETS):
            self.assertIn(
                name, carried,
                "%s is declared shipping but neither pyproject.toml nor "
                "MANIFEST.in mentions it, so the wheel and sdist will not "
                "contain it" % name)

    def test_every_console_script_target_is_callable_with_no_arguments(self):
        """A packaging entry point is invoked as target(), with no argv.
        bm_learn.main and bm_runtimes.main take a required argv, so pointing
        a script straight at them would install a command that raises
        TypeError the first time anyone runs it, and no build step would
        notice. Each target is imported and its signature checked here."""
        import inspect
        for script, target in sorted(self._scripts().items()):
            mod_name, _, attr = target.partition(":")
            self.assertTrue(attr, "%s has no attribute in its target %r"
                            % (script, target))
            self.assertIn(mod_name, self._py_modules(),
                          "%s points at %s, which pyproject.toml does not "
                          "install" % (script, mod_name))
            path = os.path.join(HERE, mod_name + ".py")
            self.assertTrue(os.path.isfile(path), "%s has no file" % mod_name)
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, attr, None)
            self.assertTrue(callable(fn),
                            "%s points at %s, which is missing or not "
                            "callable" % (script, target))
            required = [p for p in inspect.signature(fn).parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind in (p.POSITIONAL_ONLY,
                                       p.POSITIONAL_OR_KEYWORD)]
            self.assertEqual(
                [], required,
                "%s would install a broken command: %s requires argument(s) "
                "%s but an entry point is called with none"
                % (script, target, [p.name for p in required]))

    def test_packaged_version_matches_the_VERSION_file(self):
        """PEP 440 will not accept 2.0.0-rc.3, so the packaged version is
        its normalized spelling. The two are allowed to LOOK different and
        are not allowed to BE different: a wheel labelled with a version
        that is not this release is a supply-chain lie, not a typo."""
        declared = re.search(r'^version\s*=\s*"([^"]+)"', self._text(), re.M)
        self.assertIsNotNone(declared, "no version in pyproject.toml")
        repo = _read(os.path.join(self.ROOT, "VERSION")).strip()
        normalized = repo.replace("-", "").replace("rc.", "rc")
        self.assertEqual(
            normalized, declared.group(1),
            "VERSION says %r (PEP 440 spelling %r) but pyproject.toml would "
            "publish %r" % (repo, normalized, declared.group(1)))

    def test_plugin_manifest_version_matches_the_VERSION_file(self):
        """Same drift class as the pyproject check above, third copy of the
        same fact: .claude-plugin/plugin.json carries a version field, and a
        plugin labelled with a version that is not this release is the same
        supply-chain lie as a mislabelled wheel. Plugin manifests are not
        PEP 440, so here the match is exact: the manifest must say what the
        VERSION file says, character for character."""
        path = os.path.join(self.ROOT, ".claude-plugin", "plugin.json")
        self.assertTrue(os.path.isfile(path),
                        ".claude-plugin/plugin.json is missing: the plugin "
                        "packaging contract cannot be checked and must not "
                        "be assumed")
        manifest = json.loads(_read(path))
        repo = _read(os.path.join(self.ROOT, "VERSION")).strip()
        self.assertEqual(
            repo, manifest.get("version"),
            "VERSION says %r but .claude-plugin/plugin.json would ship %r"
            % (repo, manifest.get("version")))

    def test_marketplace_manifest_versions_match_the_VERSION_file(self):
        """The marketplace manifest carries the same fact in two MORE places
        (metadata.version and each listed plugin's version). Same drift
        class, same rule: every copy matches VERSION character for character,
        or the release is mislabelled somewhere a user will trust."""
        path = os.path.join(self.ROOT, ".claude-plugin", "marketplace.json")
        if not os.path.isfile(path):
            self.skipTest("no marketplace manifest in this repository")
        manifest = json.loads(_read(path))
        repo = _read(os.path.join(self.ROOT, "VERSION")).strip()
        found = []
        meta_ver = (manifest.get("metadata") or {}).get("version")
        if meta_ver is not None:
            found.append(("metadata.version", meta_ver))
        for i, plug in enumerate(manifest.get("plugins") or []):
            if plug.get("version") is not None:
                found.append(("plugins[%d].version" % i, plug.get("version")))
        self.assertTrue(found,
                        "marketplace.json exists but carries no version "
                        "field anywhere; the consistency contract cannot "
                        "be checked and must not be assumed")
        for where, val in found:
            self.assertEqual(
                repo, val,
                "VERSION says %r but marketplace.json %s says %r"
                % (repo, where, val))

    def test_the_package_declares_no_dependencies(self):
        """Standard library only is an invariant of this project, not a
        current state of affairs. If a dependency ever appears here it must
        be because the founder decided to take one, and this test failing is
        how that decision gets noticed."""
        m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", self._text(), re.S | re.M)
        self.assertIsNotNone(m, "no dependencies key in pyproject.toml")
        self.assertEqual(
            [], re.findall(r'"([^"]+)"', m.group(1)),
            "pyproject.toml declares dependencies; BrotherMode ships "
            "standard library only")


class TestTheGuidedLoopLawIsWrittenAndWired(unittest.TestCase):
    """Ratified 2026-08-01: guidance and judgment sit on the strongest
    capability profile, execution routes down to the cheapest profile that
    passes the done-check. That routing only survives if it is written law
    that other pages point at, not a preference one session remembered. This
    suite pins the three places the law must exist and agree: the delegation
    reference that states it, the beginner conductor that translates it, and
    the profiles reference that carries the routing language it uses. Any of
    the three drifting silently is exactly the failure this project logs as
    a rule in a prompt rather than a control that runs."""

    ROOT = os.path.dirname(HERE)

    def _text(self, *parts):
        path = os.path.join(self.ROOT, *parts)
        self.assertTrue(os.path.isfile(path),
                        "%s is missing; the guided-loop law cannot be "
                        "checked and must not be assumed" % os.path.join(*parts))
        return _read(path)

    def test_the_delegation_reference_states_the_loop_and_its_stages(self):
        text = self._text("references", "delegation.md")
        self.assertIn("## The guided loop", text,
                      "references/delegation.md lost the guided-loop section")
        for stage in ("GUIDE.", "EXECUTE.", "VERIFY.", "LAND."):
            self.assertIn(stage, text,
                          "the guided loop lost its %s stage" % stage.rstrip("."))
        self.assertIn("Escalation rule", text,
                      "the loop without its escalation rule loops a failing "
                      "executor forever, which is the defect the rule exists "
                      "to prevent")

    def test_the_beginner_conductor_points_at_the_loop_in_plain_language(self):
        text = self._text("skills", "brotherme", "SKILL.md")
        self.assertIn("guided loop", text,
                      "the beginner conductor no longer mentions the guided "
                      "loop; the beginner surface and the expert law have "
                      "come apart")
        self.assertIn("references/delegation.md", text,
                      "the beginner conductor must point at the file that "
                      "holds the loop law, not restate it")
        self.assertIn("picking the right helper for the job", text,
                      "the plain-language name for the loop is bound by "
                      "references/terminology.md and must appear verbatim")

    def test_the_profiles_reference_carries_the_routing_language(self):
        text = self._text("references", "profiles.md")
        for profile in ("Navigator", "Builder", "Reviewer", "Fast Worker",
                        "Vision Worker", "Researcher"):
            self.assertIn(profile, text,
                          "references/profiles.md lost the %s profile the "
                          "guided loop routes by" % profile)


class TestTheSeventhCommandAndTheDeepTourAreWired(unittest.TestCase):
    """Wave 8, 2026-08-01. Three claims this release makes to a beginner,
    each of which would fail silently without a pin: that /brotherme-update
    exists and teaches the VERIFIED update lines rather than invented ones;
    that the help command's deep tour enters a flow the conductor actually
    defines (the rc.10 audit found a command entering a flow nobody
    defined, and this suite is why that class of defect stays dead); and
    that the explainer page still carries its honesty label after being
    rebuilt for excitement. Excitement without the label is the overclaim
    this project exists to refuse."""

    ROOT = os.path.dirname(HERE)

    def _text(self, *parts):
        path = os.path.join(self.ROOT, *parts)
        self.assertTrue(os.path.isfile(path),
                        "%s is missing" % os.path.join(*parts))
        return _read(path)

    def test_exactly_seven_brotherme_commands_ship(self):
        # Named for the original seven beginner commands. L03 added the three
        # Full-Auto controller commands (auto, auto-status, stop), a deliberate
        # new family, so the pinned set became ten. L04 adds four more (brief,
        # decisions, handback, handover-pack), the founder-mode family, so it
        # is now fourteen. L05 adds one (view, the live project page), the
        # visual surface's single command, so it is now fifteen.
        # The NAME of this test is two families out of date and is deliberately
        # not changed: it is cited by name in the closure register and in the
        # evidence pages, and renaming it would break those citations to fix
        # nothing. What the test does is unchanged and is the whole point: a
        # command file nobody meant to ship fails it, and every growth of the
        # set costs somebody a sentence here rather than landing quietly.
        found = sorted(os.path.basename(p) for p in
                       __import__("glob").glob(
                           os.path.join(self.ROOT, "commands",
                                        "brotherme-*.md")))
        expected = ["brotherme-auto-status.md", "brotherme-auto.md",
                    "brotherme-brief.md", "brotherme-decisions.md",
                    "brotherme-deliver.md", "brotherme-handback.md",
                    "brotherme-handover-pack.md", "brotherme-help.md",
                    "brotherme-next.md", "brotherme-review.md",
                    "brotherme-start.md", "brotherme-status.md",
                    "brotherme-stop.md", "brotherme-update.md",
                    "brotherme-view.md"]
        self.assertEqual(expected, found,
                         "the shipped command set drifted from the fifteen "
                         "this release documents (seven beginner, three "
                         "controller, four founder mode, one visual "
                         "surface): %r" % found)

    def test_the_five_store_backed_commands_name_the_mechanical_command(self):
        """Loop 2 WP-C, decision D-3 (docs/superpowers/specs/2026-08-01-
        loop2-mechanical-commands-design.md): the five store-backed skill
        files must literally name their mechanical invocation, not just
        describe a flow in prose. A skill file that never names the
        mechanical command leaves the model free to answer from memory of
        the conversation instead of running the one thing that actually
        knows the project's state.

        H3 INVERSION, 2026-08-08 (V3-FREEZE-2026-08-07.md ruling H3, item 1;
        authorized by freeze answer 4, "Skills call ONLY it [the
        brothermode CLI]. tools/bm_*.py become internal adapters behind
        it"): this test used to pin `python3 tools/bm_project.py <verb>`
        inside the five legacy commands/brotherme-*.md files, mandating the
        exact pre-v3 wiring the freeze retires on purpose. The protected
        behavior (a beginner-facing file must name its real mechanical
        command rather than let the model answer from memory) is retired
        for NOTHING and moves house: it now pins the v3 canonical
        skills/<name>/SKILL.md files against their brothermode CLI
        invocation instead of the retired direct tool call. This is the
        rewrite ruling H3 names as closure under plan section 17
        (inversion, not deletion, of a pin whose protected behavior is
        deliberately retired)."""
        expected = {
            "start": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py\" start",
            "status": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py\" status",
            "next": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py\" next",
            "review": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py\" review",
            "deliver": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py\" deliver",
        }
        for skill_name, invocation in expected.items():
            text = self._text("skills", skill_name, "SKILL.md")
            self.assertIn(invocation, text,
                          "skills/%s/SKILL.md lost its mechanical command "
                          "%r; a beginner skill file that does not name "
                          "the real command it runs leaves the model free "
                          "to answer from memory instead"
                          % (skill_name, invocation))

    def test_the_update_command_teaches_only_verified_lines(self):
        """H-3, 2026-08-08: this pin used to check commands/brotherme-update.md
        for the pre-rename `/plugin marketplace update brotherme-marketplace`
        and `/plugin update brotherme` lines. Those lines cannot work for a v3
        install: a marketplace update on the old plugin id `brotherme` never
        becomes the new id `brothermode`, they are two different
        registrations to Claude Code, and skills/update/SKILL.md itself says
        so and refuses to send anyone through them. Pinning them as
        'verified' was pinning a known-broken instruction. The check now
        follows the same command's canonical v3 home, skills/update/SKILL.md,
        and pins the lines that file actually teaches: uninstall the old
        plugin, then add and install the new one fresh, both by their real
        names."""
        text = self._text("skills", "update", "SKILL.md")
        for line in ("/plugin uninstall brotherme@brotherme-marketplace",
                     "/plugin marketplace add khalilmaaouni/BrotherModeUp@<released-tag>",
                     "/plugin install brothermode@brothermode-marketplace",
                     "git fetch --tags"):
            self.assertIn(line, text,
                          "skills/update/SKILL.md lost the verified update "
                          "line %r; a beginner following this command "
                          "would be typing something nobody checked" % line)

    def test_the_deep_tour_flow_exists_on_both_sides(self):
        command = self._text("commands", "brotherme-help.md")
        conductor = self._text("skills", "brotherme", "SKILL.md")
        self.assertIn("deep tour", command,
                      "the help command no longer offers the deep tour")
        self.assertIn("## Deep tour flow", conductor,
                      "the conductor lost the deep-tour flow the help "
                      "command enters; a command entering an undefined "
                      "flow is the exact rc.10 audit defect")
        self.assertIn("bm_docs.py generate", conductor,
                      "the deep tour no longer names the docs engine "
                      "that builds its live view")

    def test_the_explainer_keeps_its_honesty_label(self):
        text = self._text("docs", "brotherme-explained.html")
        self.assertIn("This conversation is an illustration", text,
                      "the walkthrough's honesty label is gone; the page "
                      "may not present composed bubbles as a recording")
        self.assertIn("brotherme-update", text,
                      "the explainer no longer teaches updating")

    def test_the_explainer_features_section_declares_its_update_rule(self):
        """Founder directive 2026-08-01: every release that changes a feature
        updates the explainer's features section in the same change. The rule
        only binds if the page still carries the section and still declares
        the rule; a page that silently dropped either has already broken it."""
        text = self._text("docs", "brotherme-explained.html")
        self.assertIn("What makes it different", text,
                      "the explainer lost its features section")
        self.assertIn("kept current by rule", text,
                      "the features section no longer declares the "
                      "same-change update rule docs/RELEASE.md step 2 binds "
                      "releases to")

    def test_the_explainer_states_the_founding_belief(self):
        """Rewritten 2026-08-01 after the founder's 1/5: the full ten-laws
        list moved to the book (the summary was restating eight ideas
        thirty-one times), but the founding belief stays on the page in one
        sentence."""
        text = self._text("docs", "brotherme-explained.html")
        self.assertIn("a rule written in a prompt is not a control", text,
                      "the founding belief sentence is gone from the "
                      "summary page")

    def test_the_summary_stays_a_summary(self):
        """The usefulness gate, mechanical half. Findings behind it, from
        the 2026-08-01 red team: 4,942 words with the install command at 90
        percent depth rated 1/5 by the founder. The caps are proxies: a
        page under the cap can still be weak, but a page over it has
        regrown the wall of prose."""
        import re
        raw = self._text("docs", "brotherme-explained.html")
        body = re.sub(r"<style.*?</style>", "", raw, flags=re.S)
        words = len(re.sub(r"<[^>]+>", " ", body).split())
        self.assertLess(words, 1600,
                        "the summary has regrown to %d body words; the "
                        "rebuild spec caps it near 1,500" % words)
        lines = raw.split("\n")
        hit = next((i for i, ln in enumerate(lines, 1)
                    if "plugin install brotherme" in ln), None)
        self.assertIsNotNone(hit, "the install line left the summary")
        body_start = next((i for i, ln in enumerate(lines, 1)
                           if "</style>" in ln), 0)
        self.assertLess(hit - body_start, 60,
                        "the install line sits %d body lines down; the "
                        "spec puts it above the fold" % (hit - body_start))


class TestP18FixApprovalReferenceIsTheFoundersOwn(unittest.TestCase):
    """LOOP P18-fix. The launch drafts say a correction becomes a rule only
    when you approve it by hand WITH A REASON RECORDED. The store enforced
    "some founder_ref is present"; the command line satisfied that guard on
    its own behalf with the string "bm_learn.py approve, run by the founder at
    <timestamp>" whenever --ref was omitted. Reproduced by hand on 68eb4d8:
    capture, then `bm_learn.py approve <id>` with no --ref, exit 0, live rule.
    A guard a tool can satisfy for itself is not a guard, and public copy
    asserting a control the code does not have is the claim class the drafts'
    own preamble forbids."""

    def _learn(self, root, args):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        return subprocess.run([sys.executable, os.path.join(HERE, "bm_learn.py")]
                              + args, cwd=root, env=env, capture_output=True,
                              text=True)

    def _init(self, root):
        env = dict(os.environ, BROTHERMODE_ROOT=root)
        r = subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"),
                            "init"], cwd=root, env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _candidate(self, root):
        out = self._learn(root, ["capture", "--scope", "global", "--json",
                                 "--trigger", "reviewing swift code",
                                 "--action", "run swiftlint",
                                 "--because", "style"])
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)["candidate_uuid"]

    def test_approve_without_a_reference_refuses_and_creates_no_rule(self):
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            out = self._learn(root, ["approve", cid])
            self.assertEqual(out.returncode, 2,
                             "approve without --ref must refuse: %s%s"
                             % (out.stdout, out.stderr))
            self.assertIn("--ref", out.stdout + out.stderr)
            rules = self._learn(root, ["rules", "--json"])
            self.assertEqual(json.loads(rules.stdout), [],
                             "a refused approval must leave no rule behind")

    def test_a_whitespace_reference_is_not_a_reference(self):
        """The refusal is on CONTENT, not on the flag being typed. Accepting
        --ref "   " would restore the same hole with one more keystroke."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            out = self._learn(root, ["approve", cid, "--ref", "   "])
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_approve_with_a_reference_still_works(self):
        """The guard is calibrated only if the good path still passes."""
        with tempfile.TemporaryDirectory() as root:
            self._init(root)
            cid = self._candidate(root)
            g = self._learn(root, ["grant-approval", cid, "--answer",
                                   "yes, run swiftlint", "--json"])
            self.assertEqual(g.returncode, 0, g.stderr)
            token = json.loads(g.stdout)["token"]
            out = self._learn(root, ["approve", cid, "--receipt", token, "--ref",
                                     "I said this in session 2026-07-29"])
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            rules = json.loads(self._learn(root, ["rules", "--json"]).stdout)
            self.assertEqual(len(rules), 1)
            # The founder's words are the recorded evidence, verbatim.
            why = self._learn(root, ["why", rules[0]["rule_uuid"]])
            self.assertEqual(why.returncode, 0, why.stderr)
            # The founder's words are the recorded evidence, verbatim. The
            # receipt id sits in front of them since post-audit LOOP 3, so the
            # assertion is on the words rather than on the whole line.
            self.assertIn("founder approval (receipt", why.stdout)
            self.assertIn("I said this in session 2026-07-29", why.stdout)
            self.assertIn("support founder_approval", why.stdout)
            self.assertEqual(
                why.stdout.count("I said this in session 2026-07-29"), 2,
                "the founder's own words must reach both the version line and "
                "the evidence line")

    def test_no_shipping_tool_fabricates_a_founder_reference(self):
        """Structural, because the same shortcut can be reintroduced in any
        other command that approves. No tool may build a founder reference out
        of a timestamp or its own name. Checked over STRING LITERALS via the
        AST, not over the file text, so the comment explaining the old bug
        does not trip its own guard."""
        for name in ("bm_learn.py", "bm_store.py"):
            tree = ast.parse(_read(os.path.join(HERE, name)))
            # Docstrings are excluded, the way the sibling sweep in
            # test_bm_store.py excludes them: the module docstring of
            # bm_learn.py QUOTES the old fabricated reference while explaining
            # why it is gone, and a guard that forbids describing the bug it
            # closed teaches people to delete the explanation.
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    body = node.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)):
                    continue
                if id(node) in docstrings:
                    continue
                self.assertNotIn(
                    "run by the founder", node.value,
                    "%s line %s builds a founder reference for the founder"
                    % (name, getattr(node, "lineno", "?")))


class TestP18FixBenchmarkArgumentsCannotBuyAnUnearnedGreen(unittest.TestCase):
    """LOOP P18-fix. scripts/benchmark.py read selectors as `int(a) for a in
    argv if a.isdigit()` and never checked them against the scenario count,
    and its exit code treated an empty result list as success. Reproduced by
    hand on 68eb4d8: `python3 scripts/benchmark.py 99 --quiet` printed
    "BENCHMARK: 0 passed, 0 failed, 0 skipped, of 0 run" and exited 0, so
    anything wiring `benchmark.py $N` got a green as proof that scenario N
    passed. A mistyped flag such as --quite was discarded just as quietly.

    These run the harness with bad arguments only. They never run a scenario,
    so they stay fast and add no subprocess-heavy scenario work to the suite."""

    BENCH = os.path.join(os.path.dirname(HERE), "scripts", "benchmark.py")

    def _run(self, args):
        return subprocess.run([sys.executable, self.BENCH] + args,
                              capture_output=True, text=True)

    def test_a_scenario_number_that_does_not_exist_is_refused(self):
        for arg in ("99", "0", "-1"):
            out = self._run([arg, "--quiet"])
            self.assertEqual(out.returncode, 2,
                             "benchmark.py %s exited %d: %s"
                             % (arg, out.returncode, out.stdout))
            self.assertNotIn("0 passed, 0 failed", out.stdout,
                             "a refused selector must not print a score line")

    def test_an_unknown_option_is_refused_not_discarded(self):
        out = self._run(["--quite"])
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("unknown option", out.stdout)
        out = self._run(["foo"])
        self.assertEqual(out.returncode, 2, out.stdout)

    def test_the_only_supported_option_still_parses(self):
        """Calibration: the refusal above must not have eaten --quiet. Runs
        one real scenario, the cheapest of the thirteen, so the good path is
        proven rather than assumed."""
        out = self._run(["1", "--quiet"])
        self.assertIn(out.returncode, (0, 1),
                      "a valid invocation must not be refused: %s" % out.stdout)
        self.assertIn("of 1 run", out.stdout)


class TestFenceLintRecognizesStoreRenderedFences(unittest.TestCase):
    """D3 (fence sweep, 2026-07-30). bm_telemetry.py's cmd_fence_lint used to
    match ONLY hand-written V1 fence lines (a "- " line containing the word
    "agent"), so it reported "no live fences found" against a STATE.md
    holding a REAL, store-rendered active claim.

    Reproduced against a throwaway store before the fix:
        $ bm_telemetry.py fence-lint .
        fence-lint: no live fences found under .
    even though STATE.md held exactly one active claim. The fix teaches
    cmd_fence_lint the exact record-line shape render_state_md (bm_store.py)
    writes today, derived by reading that renderer, and uses the STATE
    SECTION HEADER ("## active"/"## parked" vs "## complete"/"## adopted"),
    not per-line text, to tell a live record apart from a finished one."""

    def setUp(self):
        # BROTHERMODE_REGISTRIES points this checkout's own dev environment
        # at real project STATE.md files; scrubbed so a test asserting "no
        # live fences" cannot be polluted (or falsely passed) by whatever
        # ambient project state happens to be lying around outside the
        # throwaway store this test builds.
        self._old_registries = os.environ.pop("BROTHERMODE_REGISTRIES", None)

    def tearDown(self):
        if self._old_registries is not None:
            os.environ["BROTHERMODE_REGISTRIES"] = self._old_registries

    def test_fence_lint_sees_a_live_store_rendered_claim(self):
        with tempfile.TemporaryDirectory() as d:
            bs.init_project(d).close()
            store = bs.Store(d, create=False)
            try:
                store.claim("myrec", "ephemeral", "test D3", ["foo.py"], tier="T2")
            finally:
                store.close()
            bs.write_state_view(d)
            code, out = _call_thread_cmd_in_process(d, bm.cmd_fence_lint, [d])
        self.assertEqual(code, 0)
        self.assertIn("LIVE FENCES", out)
        self.assertIn("myrec", out)

    def test_fence_lint_ignores_a_completed_record(self):
        # A record moved to "complete" must NOT show up as live, mirroring
        # the old V1 rule's LANDED/ADOPTED exclusion for the new format --
        # its state now lives in the SECTION HEADER, not inline text.
        with tempfile.TemporaryDirectory() as d:
            bs.init_project(d).close()
            store = bs.Store(d, create=False)
            try:
                rec = store.claim("donerec", "ephemeral", "finish D3", ["bar.py"],
                                   session_id="s1")
                store.transition(rec.lifecycle_uuid, rec.version, "complete",
                                  session_id="s1", evidence="shipped")
            finally:
                store.close()
            bs.write_state_view(d)
            code, out = _call_thread_cmd_in_process(d, bm.cmd_fence_lint, [d])
        self.assertEqual(code, 0)
        self.assertNotIn("donerec", out)
        self.assertIn("no live fences found", out)

    def test_calibrated_pre_fix_fence_lint_misses_a_pre_m12_store_rendered_claim(self):
        """CALIBRATION: reinject the exact pre-fix cmd_fence_lint body
        (matching ONLY the V1 "- ... agent ..." shape) onto the real
        bm_telemetry module object, in-process, and confirm the SAME live
        claim the first test above sees is invisible to it when the
        registry carries the line shape render_state_md wrote BEFORE M12
        -- proving the section-header + regex recognition D3's real fix
        added is what finds it, not something about this test's setup.

        UPDATED FOR M12 (docs/plan/QUEUE.json): render_state_md now adds
        the literal word "agent" to an active/parked bullet's own first
        line (the fix for a DIFFERENT bug: the INSTALLED BrotherSBE
        reconcile required that word and could not see a store claim
        without it). That is a deliberate, wanted side effect: even the
        naive V1-only matcher below now ALSO recognizes a
        currently-rendered claim, because the word really is there now.
        This test therefore builds its OWN registry text, byte for byte
        the shape render_state_md wrote before M12 (no "agent" field;
        see this file's own git history, or bm_telemetry.py's comment
        directly above _STORE_RECORD_LINE_RE, which still documents that
        shape), so the calibration keeps proving what it always proved
        about that shape, independent of what the live renderer emits
        today."""
        def _pre_fix_cmd_fence_lint(argv):
            cwd = argv[0] if argv else ""
            if not cwd and not sys.stdin.isatty():
                try:
                    cwd = (json.load(sys.stdin) or {}).get("cwd", "")
                except Exception:
                    cwd = ""
            cwd = cwd or os.getcwd()
            hits = []
            pats = [os.path.join(cwd, "STATE.md")]
            pats += [p.strip() for p in
                     os.environ.get("BROTHERMODE_REGISTRIES", "").split(":") if p.strip()]
            for pat in pats:
                for p in glob.glob(pat, recursive=True):
                    for line in open(p, errors="replace"):
                        s = line.strip()
                        if (s.startswith("- ") and "agent" in s.lower()
                                and "LANDED" not in s and "ADOPTED" not in s):
                            tier = ("" if re.search(r"\btier T[123]\b", s)
                                     else " [NO-TIER: declare T1/T2/T3]")
                            hits.append("%s: %s%s" % (os.path.basename(p), s[:100], tier))
            if hits:
                print("LIVE FENCES (fence-then-dispatch; overlap means queue):")
                for h in hits[:8]:
                    print("  " + h)
            else:
                print("fence-lint: no live fences found under %s" % cwd)

        real = bm.cmd_fence_lint
        try:
            with tempfile.TemporaryDirectory() as d:
                bs.init_project(d).close()
                # The PRE-M12 shape, hand-written rather than rendered:
                # "- name (uuid, version N, lifetime) [tier] owner-session:
                # id", no "agent" field. This is exactly the text
                # render_state_md produced for an active record before
                # M12 added the agent field for BrotherSBE's sake.
                state_path = os.path.join(d, "STATE.md")
                with io.open(state_path, "w", encoding="utf-8") as fh:
                    fh.write(
                        "## active\n"
                        "- myrec (1f7ee86a84024b5fa16ce01a88bb4489, "
                        "version 1, ephemeral) [T2] owner-session: no "
                        "session\n"
                        "  objective: test D3\n"
                        "  files: foo.py\n")
                bm.cmd_fence_lint = _pre_fix_cmd_fence_lint
                code, out = _call_thread_cmd_in_process(d, bm.cmd_fence_lint, [d])
        finally:
            bm.cmd_fence_lint = real
        self.assertIn(
            "no live fences found", out,
            "REINJECTION CHECK: the pre-fix V1-only matcher must miss the "
            "pre-M12 store-rendered claim shape, proving the section/"
            "regex recognition added by the real D3 fix is what finds it")
        self.assertNotIn("myrec", out)

    def test_m12_naive_matcher_also_now_sees_the_current_render(self):
        """M12's own regression pin, the mirror of the calibration above:
        the CURRENT render_state_md output for an active claim contains
        the word "agent" on its own first line, so even the naive,
        pre-D3 V1-only matcher recognizes it now (a deliberate, wanted
        side effect, since it means the INSTALLED BrotherSBE reconcile,
        which uses the same "agent" substring rule, sees it too).
        REPRODUCED pre-fix: this exact assertion failed, because
        render_state_md's own line carried no "agent" substring at all
        (bm_telemetry.py's own comment right above _STORE_RECORD_LINE_RE
        says so in as many words)."""
        with tempfile.TemporaryDirectory() as d:
            bs.init_project(d).close()
            store = bs.Store(d, create=False)
            try:
                store.claim("myrec", "ephemeral", "test M12", ["foo.py"],
                            tier="T2")
            finally:
                store.close()
            bs.write_state_view(d)
            state_path = os.path.join(d, "STATE.md")
            with io.open(state_path, encoding="utf-8") as fh:
                text = fh.read()
        first_bullet = next(
            line for line in text.splitlines() if line.startswith("- myrec"))
        self.assertIn("agent", first_bullet.lower())


class TestGitignoreCoversGeneratedProjectViews(unittest.TestCase):
    """V2 (release-closure loop2 refuter fixes): tools/bm_project.py's
    generated views (CANVAS.md, DELIVERY-PACKET.md, and their
    multi-project, project-scoped forms from C5) must never be committed
    to THIS repository, the same reason STATE.md and Documentation/
    already are not: they render one machine's live project rows, not
    shared repository content."""

    ROOT = os.path.dirname(HERE)

    def test_gitignore_lists_all_four_generated_view_patterns(self):
        with io.open(os.path.join(self.ROOT, ".gitignore"),
                     encoding="utf-8") as fh:
            lines = {line.strip() for line in fh}
        for pattern in ("/CANVAS.md", "/DELIVERY-PACKET.md",
                        "/CANVAS-*.md", "/DELIVERY-PACKET-*.md",
                        "/PROJECT-VIEW.html", "/Handover/", "/Handover-*/"):
            self.assertIn(
                pattern, lines,
                ".gitignore lost the generated-view pattern %r; a "
                "founder's own project rows could land in a commit to "
                "this repository" % pattern)

    def test_manifest_scripts_exclude_the_same_generated_view_family(self):
        # Found live 2026-08-06 by dogfooding: bm_project.py start with
        # --allow-second wrote CANVAS-<project-id>.md at the repository
        # root, .gitignore hid it from git, and verify-install.sh then
        # FAILED with an EXTRA entry, because the two manifest scripts
        # excluded only the exact name CANVAS.md. CANVAS.md itself went
        # through this same class once already (the dated comment inside
        # scripts/checksums.sh tells that story), so this pin holds the
        # WHOLE gitignore family in both scripts rather than one name.
        #
        # E80 (2026-09-04) narrowed the FORM, never the family: the same
        # five names are excluded, but ROOT-ANCHORED rather than bare,
        # because a bare name also matched the two tracked template files
        # project-template/CANVAS.md and project-template/DELIVERY-PACKET.md
        # and dropped them out of the release. This pin now holds the
        # root-anchored form, so a revert to the bare name fails here.
        anchors = {"scripts/checksums.sh": "! -path './%s'",
                   "scripts/verify-install.sh": '! -path "$TARGET/%s"'}
        for script in ("scripts/checksums.sh", "scripts/verify-install.sh"):
            with io.open(os.path.join(self.ROOT, script),
                         encoding="utf-8") as fh:
                src = fh.read()
            # The negative below reads OPERAND lines only, never comments: a
            # comment explaining why the bare form was wrong necessarily
            # quotes the bare form, and matching that would fail on the very
            # file that documents the fix.
            operands = "\n".join(
                line for line in src.splitlines()
                if not line.lstrip().startswith("#"))
            for name in ("CANVAS.md", "CANVAS-*.md",
                         "DELIVERY-PACKET.md", "DELIVERY-PACKET-*.md",
                         "PROJECT-VIEW.html"):
                self.assertIn(
                    anchors[script] % name, src,
                    "%s no longer excludes the generated view %r at the "
                    "install root; any machine with a generated project "
                    "view at the root fails its own integrity check with "
                    "an EXTRA warning" % (script, name))
                self.assertNotIn(
                    "! -name '%s'" % name, operands,
                    "%s excludes %r by BARE NAME again; that also matches "
                    "the tracked template project-template/%s and drops a "
                    "shipped file out of the release (E80)"
                    % (script, name, name))
            # The L05 brief pages land in a Handover/ directory, pruned by
            # path rather than by name; both scripts must prune it or a
            # generated brief page fails verify-install the same way.
            self.assertIn("Handover", src,
                          "%s no longer prunes the Handover/ brief "
                          "directory" % script)


class TestScorecardSurvivesANullTokenField(unittest.TestCase):
    """N-6 finding 1 (2026-08-04). A ledger row that parses as valid JSON but
    carries `null` where a token count belongs used to make fld() return
    None unchanged, the out7 sum at cmd_scorecard raise TypeError, and the
    blanket handler in main() swallow it -- one cryptic line instead of all
    nine metrics, and read_jsonl's malformed-line reporting could never
    help because the row parses fine."""

    def test_null_output_tokens_does_not_collapse_the_scorecard(self):
        with tempfile.TemporaryDirectory() as v:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            ledger = os.path.join(teld, "outcomes.jsonl")
            rows = [
                {"schema": 2, "ts": bm.now_iso(), "session_id": "s1", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": 100,
                 "gen_ai.usage.input_tokens": 50},
                {"schema": 2, "ts": bm.now_iso(), "session_id": "s2", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": None,
                 "gen_ai.usage.input_tokens": 50},
            ]
            with io.open(ledger, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("BROTHERMODE SCORECARD", r.stdout,
                          "a null token field must not collapse the scorecard to one line")
            self.assertNotIn("swallowed error", r.stdout,
                             "the null token field crashed and was swallowed by the "
                             "blanket handler instead of being handled")


class TestScorecardDisclosesMalformedSignalsLines(unittest.TestCase):
    """N-6 finding 2 (2026-08-04). cmd_scorecard reads outcomes, ratings,
    reviews and corrections with report_bad=True and feeds the WARNING
    line; signals.jsonl was read without it, so a corrupt signals line
    silently lowered the rework/escaped-defect counts (metrics 4 and 6)
    while the WARNING line, which exists to disclose exactly this, said
    nothing."""

    def test_malformed_signals_line_is_named_in_the_warning(self):
        with tempfile.TemporaryDirectory() as v:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            signals = os.path.join(teld, "signals.jsonl")
            good1 = {"ts": bm.now_iso(), "kind": "rework", "session_id": "s1"}
            good2 = {"ts": bm.now_iso(), "kind": "escaped-defect", "session_id": "s2"}
            with io.open(signals, "w", encoding="utf-8") as f:
                f.write(json.dumps(good1) + "\n")
                f.write('{"kind": "rework", "session_id": "truncat\n')  # malformed
                f.write(json.dumps(good2) + "\n")
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("WARNING", r.stdout,
                          "a malformed signals.jsonl line must trigger the WARNING line")
            self.assertIn("signals=1", r.stdout,
                          "the WARNING must name how many signals lines were dropped")


class TestSpeedAndStartupNagsDiscloseMalformedLedgerLines(unittest.TestCase):
    """N-6 finding 3 (2026-08-04). cmd_migrate and cmd_dedup disclose
    malformed lines; cmd_speed and cmd_startup_nags both call
    read_jsonl(LEDGER) without report_bad, so they publish session counts,
    span-hours and token sums computed over a silently reduced ledger while
    cmd_scorecard, reading the SAME file, prints a WARNING."""

    def _ledger_with_one_bad_line(self, v):
        teld = os.path.join(v, "99-System", "telemetry")
        os.makedirs(teld, exist_ok=True)
        ledger = os.path.join(teld, "outcomes.jsonl")
        good1 = {"schema": 2, "ts": bm.now_iso(), "session_id": "s1", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": 1500,
                 "gen_ai.usage.input_tokens": 10, "duration_h": 1.0}
        good2 = {"schema": 2, "ts": bm.now_iso(), "session_id": "s2", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": 1500,
                 "gen_ai.usage.input_tokens": 10, "duration_h": 2.0}
        with io.open(ledger, "w", encoding="utf-8") as f:
            f.write(json.dumps(good1) + "\n")
            f.write('{"schema": 2, "session_id": "truncat\n')  # malformed
            f.write(json.dumps(good2) + "\n")
        return ledger

    def test_speed_discloses_malformed_lines(self):
        with tempfile.TemporaryDirectory() as v:
            self._ledger_with_one_bad_line(v)
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "speed"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1 malformed", r.stdout,
                          "speed must disclose the dropped ledger line count")

    def test_startup_nags_discloses_malformed_lines(self):
        with tempfile.TemporaryDirectory() as v:
            self._ledger_with_one_bad_line(v)
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "startup-nags"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1 malformed", r.stdout,
                          "startup-nags must disclose the dropped ledger line count")


class TestProjectIdentityDegradesLoudlyWithoutBmStore(unittest.TestCase):
    """N-6 finding 4 (2026-08-04). _resolve_root_quiet returns (None, None)
    on ANY exception, including bm_store.py being unimportable, and
    _project_of then silently falls back to _legacy_project_of -- the exact
    per-folder-collision formula the 2026-07-26 round removed, with nothing
    printed. This forces bm._get_bm_store() to report unavailable (the same
    calibration shape TestCompactHintHonesty uses for bm_autosave, patching
    the CACHED module reference rather than the file) and checks that the
    degradation is now labelled, matching this file's own law that the
    honest form of never blocking is a labelled degradation, not a silent
    one."""

    def test_identity_fallback_prints_a_degradation_notice(self):
        old_cache = list(bm._bm_store_cache)
        bm._bm_store_cache[:] = [None, "simulated: bm_store.py unavailable"]
        old_stderr = sys.stderr
        sys.stderr = captured = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as d:
                deep = os.path.join(d, "deep", "sub")
                os.makedirs(deep)
                bm._project_of(d)
                bm._project_of(deep)
        finally:
            sys.stderr = old_stderr
            bm._bm_store_cache[:] = old_cache
        err = captured.getvalue()
        self.assertIn("bm_store.py could not be loaded", err,
                      "falling back to the legacy per-folder identity must say so, "
                      "not degrade silently")


class TestCoordinationCollisionsIndependentOfVerifyWording(unittest.TestCase):
    """N-6 finding 5 (2026-08-04). _coordination_collisions used to count
    bm_store.verify()'s problem strings by matching the literal prefix
    'active claims overlap', so an ordinary wording edit to that message in
    tools/bm_store.py would silently drop the count to 0 while verify()
    itself still reported the problem. tools/bm_store.py is outside this
    fix's write fence (a hard, program-enforced single-writer boundary), so
    the fix stays entirely inside bm_telemetry.py: _coordination_collisions
    now runs the SAME invariant verify() runs (two ACTIVE claims whose
    paths overlap) directly against the store via the public
    bm_store.ReadOnlyStore and bm_store.paths_overlap, instead of parsing
    verify()'s prose, so a wording change there can no longer move this
    number either way.

    Proven by monkeypatching the CACHED bm_store module object
    bm_telemetry.py itself loaded (bm._bm_store_cache[0], a separate module
    instance from bs, the same calibration shape TestCompactHintHonesty
    uses for bm_autosave) -- forcing verify() to report a reworded message
    and checking the collision count is unaffected."""

    def test_collision_count_survives_a_verify_wording_change(self):
        with tempfile.TemporaryDirectory() as d:
            store = bs.Store(d)
            try:
                store.claim("one", "ephemeral", "obj", ["a/b.py"], session_id="s1")
                # Simulate the corruption the API itself would never
                # produce (the store's claim() refuses an overlapping
                # claim): a second active record whose claim overlaps the
                # first, inserted directly at the SQL layer, the same
                # technique test_bm_store.py's own TestVerify uses.
                conn = store.conn
                conn.execute("BEGIN IMMEDIATE")
                ts = bs.now_iso()
                conn.execute(
                    "INSERT INTO records (lifecycle_uuid, name, lifetime, state, "
                    "objective, owner, session_id, tier, check_cmd, evidence, "
                    "version, created_at, updated_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("deadbeefcafe", "two", "ephemeral", "active", "obj2", "", "s2",
                     "", "", "", 1, ts, ts))
                conn.execute(
                    "INSERT INTO claims (lifecycle_uuid, path) VALUES (?,?)",
                    ("deadbeefcafe", "a/b.py"))
                conn.execute("COMMIT")
            finally:
                store.close()
            mod, err = bm._get_bm_store()
            self.assertIsNotNone(mod, "bm_store.py failed to load: %s" % err)
            old_verify = mod.verify

            def _reworded_verify(root):
                problems = old_verify(root)
                return [p.replace("active claims overlap",
                                   "claims collide over a shared path") for p in problems]
            mod.verify = _reworded_verify
            try:
                n, note = bm._coordination_collisions(d)
            finally:
                mod.verify = old_verify
            self.assertIsNone(note, note)
            self.assertEqual(n, 1, "a wording change to verify()'s prose must not "
                                    "change the collision count")


class TestCorrectionCapDropDiscloses(unittest.TestCase):
    """N-6 finding 6 (2026-08-04). CORRECTION_CAP_PER_SESSION = 8 and the
    `break` in scan_corrections stop capture at eight with nothing recorded
    that more existed, directly against the docstring at line 491 to 504,
    which says the Loop 4 redesign was driven by corrections being dropped
    SILENTLY. grep -c -i -E "CORRECTION_CAP|correction cap|cap_per_session"
    tools/test_bm.py returned 0 before this test: the cap was untested."""

    def test_more_than_eight_corrections_disclose_the_drop_count(self):
        texts = ["No, that is not what I asked for. From now on always use "
                 "option %d instead." % i for i in range(1, 13)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "corrections.jsonl")
            old = bm.CORRECTIONS
            bm.CORRECTIONS = path
            try:
                result = bm.scan_corrections("sess-cap", "proj", texts)
            finally:
                bm.CORRECTIONS = old
            rows = [json.loads(x) for x in _read(path).splitlines() if x.strip()]
            self.assertEqual(len(rows), 8, "the cap itself must still hold at 8")
            self.assertEqual(result, (8, 4),
                             "scan_corrections must report (captured, dropped): "
                             "8 captured of 12 distinct candidates, 4 dropped silently today")


class TestHandoffDisclosesTruncation(unittest.TestCase):
    """N-6 finding 7 (2026-08-04). _read_head caps at 6000 characters (4000
    for the latest session log, 20000 for OUTCOMES.md) and added no marker,
    so a handoff.md silently cut a teammate-facing file mid-line with no
    sign anywhere that it happened, even though the file itself says it is
    'a snapshot, not the living record'."""

    def test_an_oversized_overview_gets_a_named_truncation_marker(self):
        with tempfile.TemporaryDirectory() as v:
            base = os.path.join(v, "10-Projects", "demo", "Sessions")
            os.makedirs(base)
            proj = os.path.dirname(base)
            body = "builds X. " + ("filler line to pad this file out. " * 300)
            self.assertGreater(len(body), 6000)
            io.open(os.path.join(proj, "Overview.md"), "w").write(body)
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            with tempfile.TemporaryDirectory() as cwd:
                r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                    "handoff", "demo"], env=env, cwd=cwd,
                                   capture_output=True, text=True)
                out = os.path.join(cwd, "handoff-demo.md")
                self.assertTrue(os.path.exists(out), "handoff file not written")
                text = io.open(out).read()
                omitted = len(body) - 6000
                self.assertIn("characters omitted", text,
                              "a truncated section must say so explicitly")
                self.assertIn(str(omitted), text,
                              "the marker must name how many characters were cut")


class TestFenceLintDisclosesHiddenFences(unittest.TestCase):
    """N-6 finding 8 (2026-08-04). cmd_fence_lint prints hits[:8] with no
    count of how many were not shown, even though its own docstring says
    its job is that 'no writer launches into an occupied file set'. With
    more than eight live fences, the ninth onward vanish silently and a
    dispatcher reading the output can wrongly conclude a file set is
    free."""

    def setUp(self):
        # Same isolation TestFenceLintRecognizesStoreRenderedFences uses:
        # this checkout's own dev environment must never leak in.
        self._old_registries = os.environ.pop("BROTHERMODE_REGISTRIES", None)

    def tearDown(self):
        if self._old_registries is not None:
            os.environ["BROTHERMODE_REGISTRIES"] = self._old_registries

    def test_more_than_eight_live_fences_names_the_total(self):
        with tempfile.TemporaryDirectory() as d:
            bs.init_project(d).close()
            store = bs.Store(d, create=False)
            try:
                for i in range(12):
                    store.claim("rec%02d" % i, "ephemeral", "test finding 8",
                                ["file%02d.py" % i], tier="T2")
            finally:
                store.close()
            bs.write_state_view(d)
            code, out = _call_thread_cmd_in_process(d, bm.cmd_fence_lint, [d])
        self.assertEqual(code, 0)
        self.assertIn("LIVE FENCES", out)
        # A bare "12" substring is not safe to assert on: the printed lines
        # embed random 32-char hex lifecycle uuids, and two adjacent hex
        # digits coincidentally spelling "12" is not vanishingly unlikely
        # across 8 of them. Assert the exact structured disclosure instead.
        self.assertIn("12 live fences total", out,
                      "fence-lint must name the true total when it hides some")
        self.assertIn("4 more not shown", out,
                      "fence-lint must say how many of the 12 were hidden")


class TestConsentGateOnCheckUpdateAndIntent(unittest.TestCase):
    """N-6 finding 9 (2026-08-04). cmd_check_update creates the vault
    telemetry directory and writes installed-skill-version; cmd_intent
    writes a per-project log. Neither called _consented(), so both
    materialized the vault in a stranger's home before setup had run,
    protected only by the hook line in tools/bm_sessionstart.sh checking
    consent before invoking them -- exactly the placement this file's own
    docstring (line 1586 to 1588 before this fix) says is wrong. The
    inventory test built to catch this class (tools/test_bm_consent.py)
    reads hooks/hooks.json and never enumerates bm_sessionstart.sh's own
    invocations, so it could not see either command; this test drives both
    commands directly instead and is outside that blind spot. Both
    commands' HOME is pinned to a throwaway directory so this can never
    read a real founder consent config from the machine running it."""

    def test_check_update_writes_nothing_without_consent(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as v:
            env = dict(os.environ, HOME=home, BROTHERMODE_VAULT=v)
            env.pop("BROTHERME_CONFIG", None)
            marker = os.path.join(v, "99-System", "telemetry", "installed-skill-version")
            self.assertFalse(os.path.exists(marker))
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "check-update"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(marker),
                             "check-update wrote installed-skill-version without consent")

    def test_intent_writes_nothing_without_consent(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as v, \
             tempfile.TemporaryDirectory() as repo:
            env = dict(os.environ, HOME=home, BROTHERMODE_VAULT=v)
            env.pop("BROTHERME_CONFIG", None)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "intent", "next: X, because Y"], env=env, cwd=repo,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(v, "99-System")),
                             "intent wrote into the vault without consent")
            self.assertIn("setup is not complete", r.stdout)

    def test_intent_still_writes_once_consented(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as repo:
            env = _consented_env(repo, v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "intent", "next: X, because Y"], env=env, cwd=repo,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(glob.glob(os.path.join(v, "99-System", "telemetry", "intent-*.log")),
                            "intent must still write once setup is complete")


class TestSpeedDisclosesFutureDatedSessions(unittest.TestCase):
    """N-6 finding 10 (2026-08-04). cmd_speed's window() filters with
    lo <= (age_days(...) or 999) < hi; a row timestamped ahead of the clock
    yields a negative age, which fails both windows' lower bound, so the
    row appears in NEITHER window and nothing says so, while cmd_scorecard
    (age <= 7, no lower bound) still counts the same row. Reachability is
    clock skew, a hand-edited timestamp, or a restored backup, not an
    everyday path, but it is real."""

    def test_a_future_dated_session_is_named_not_just_dropped(self):
        with tempfile.TemporaryDirectory() as v:
            teld = os.path.join(v, "99-System", "telemetry")
            os.makedirs(teld)
            ledger = os.path.join(teld, "outcomes.jsonl")
            now = bm.now_iso()
            future = (bm.datetime.datetime.now(bm.datetime.timezone.utc)
                      + bm.datetime.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = [
                {"schema": 2, "ts": now, "session_id": "s1", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": 1000,
                 "gen_ai.usage.input_tokens": 10, "duration_h": 1.0},
                {"schema": 2, "ts": future, "session_id": "s2", "project": "p",
                 "end_reason": "other", "gen_ai.usage.output_tokens": 5000,
                 "gen_ai.usage.input_tokens": 10, "duration_h": 5.0},
            ]
            with io.open(ledger, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "speed"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1 session", r.stdout)
            self.assertIn("ahead of the clock", r.stdout,
                          "a future-dated session dropped from both windows must be "
                          "named, not silently invisible")


class TestScorecardNeverPrintsAvgNone(unittest.TestCase):
    """N-6 finding 11 (2026-08-04). Line 908 leaves avg_rating as the Python
    literal None when there are no attributed ratings, and line 937 formats
    it with %s, so metric 4 reads 'avg=None' -- the exact class metric 9
    (line 954) already handles correctly, printing 'no data' instead."""

    def test_no_attributed_ratings_prints_no_data_not_avg_none(self):
        with tempfile.TemporaryDirectory() as v:
            os.makedirs(os.path.join(v, "99-System", "telemetry"))
            env = dict(os.environ, BROTHERMODE_VAULT=v)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("avg=None", r.stdout)
            self.assertIn("avg=no data", r.stdout)


def _telemetry_env(home, vault):
    """Env for driving bm_telemetry.py against a THROWAWAY vault only. HOME is
    pinned too (not just the vault variables) so nothing here can reach the
    real home directory of whoever runs the suite, the same isolation
    TestConsentGateOnCheckUpdateAndIntent above uses."""
    return dict(os.environ, HOME=home, BROTHERMODE_VAULT=vault,
                BROTHERSBE_VAULT=vault)


def _write_telemetry(vault, name, rows):
    """A crafted telemetry jsonl in a throwaway vault. Rows are written as
    given (a string entry is written verbatim, so a test can put a line that
    is valid JSON but not a record into the file)."""
    teld = os.path.join(vault, "99-System", "telemetry")
    os.makedirs(teld, exist_ok=True)
    path = os.path.join(teld, name)
    with io.open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


def _write_ledger(vault, rows):
    return _write_telemetry(vault, "outcomes.jsonl", rows)


def _ledger_row(sid, **over):
    """One well-formed schema-2 ledger row, before a test spoils one field."""
    row = {"schema": 2, "ts": bm.now_iso(), "session_id": sid, "project": "p",
           "end_reason": "other", "gen_ai.usage.output_tokens": 1000,
           "gen_ai.usage.input_tokens": 10, "sub_out_tokens": 100,
           "cache_read": 900, "cache_write": 100, "duration_h": 1.0}
    row.update(over)
    return row


class TestNumericSiblingFieldsSurviveBadValues(unittest.TestCase):
    """A2 finding 1 (2026-08-04). fld() was hardened against a null or a
    string on OUT_KEYS and IN_KEYS, but sub_out_tokens, cache_read,
    cache_write and duration_h are written side by side by the SAME record
    literal in the same writer (bm_telemetry.py line 656 to 663) and were
    still read with a raw r.get(key, 0), which returns the stored null
    unchanged. Whatever hand edit, restored backup or older schema can put a
    null on the field that was fixed can put one on its siblings with equal
    ease, and the resulting TypeError inside the sum is swallowed by the
    blanket handler in main() into one cryptic line instead of the nine
    metrics, the speed table, or the spend line."""

    def _run(self, cmd, bad):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            _write_ledger(v, [_ledger_row("s1"), _ledger_row("s2", **bad)])
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"), cmd],
                               env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout,
                         "%s collapsed on %r instead of handling it" % (cmd, bad))
        return r.stdout

    def test_scorecard_survives_null_sibling_token_fields(self):
        out = self._run("scorecard", {"sub_out_tokens": None, "cache_read": None,
                                      "cache_write": None})
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("9 cache economy", out,
                      "the scorecard must still print every metric line, not stop "
                      "part way through")

    def test_scorecard_survives_string_sibling_token_fields(self):
        out = self._run("scorecard", {"sub_out_tokens": "lots", "cache_read": "many",
                                      "cache_write": "some"})
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("9 cache economy", out)

    def test_speed_survives_a_null_duration(self):
        out = self._run("speed", {"duration_h": None})
        self.assertIn("BROTHERMODE SPEED", out)
        self.assertIn("last 7d ", out, "the speed table must still print both windows")
        self.assertIn("prior 7d", out)

    def test_speed_survives_a_string_duration_and_sub_out_tokens(self):
        out = self._run("speed", {"duration_h": "2h", "sub_out_tokens": "lots"})
        self.assertIn("BROTHERMODE SPEED", out)
        self.assertIn("last 7d ", out)
        self.assertIn("prior 7d", out)

    def test_startup_nags_survives_a_null_sub_out_tokens(self):
        out = self._run("startup-nags", {"sub_out_tokens": None})
        self.assertIn("BROTHERMODE SPEND", out,
                      "the 24h spend line must still print")

    def test_startup_nags_survives_a_string_sub_out_tokens(self):
        out = self._run("startup-nags", {"sub_out_tokens": "lots"})
        self.assertIn("BROTHERMODE SPEND", out)


class TestValidJsonNonObjectRowsAreReportedMalformed(unittest.TestCase):
    """A2 finding 2 (2026-08-04). read_jsonl appended whatever json.loads
    returned, and a JSON scalar or array is valid JSON, so a line reading
    `null`, `123`, `"text"` or `[1,2,3]` landed in `rows` as None, int, str
    or list. Every consumer then calls rec.get(...) on it and raises
    AttributeError, which the blanket handler in main() swallows into one
    line, taking out the scorecard AND both repair commands an operator
    would reach for to fix the file. read_jsonl's own docstring promises
    'a caller doing arithmetic over rows still gets clean data'; a row that
    merely parses is not clean, so a non-record row is now counted and
    reported down the SAME malformed-line path as an unparseable line."""

    def _run(self, cmd, vault):
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"), cmd],
                               env=_telemetry_env(home, vault), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout,
                         "%s collapsed on a valid-JSON non-record row" % cmd)
        return r.stdout

    def test_scorecard_reports_a_bare_null_ledger_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1"), "null"])
            out = self._run("scorecard", v)
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("9 cache economy", out)
        self.assertIn("ledger=1", out,
                      "the bare null row must be counted in the malformed disclosure")

    def test_scorecard_reports_a_json_array_ledger_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1"), "[1,2,3]"])
            out = self._run("scorecard", v)
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("9 cache economy", out)
        self.assertIn("ledger=1", out)

    def test_scorecard_reports_a_bare_null_signals_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1")])
            _write_telemetry(v, "signals.jsonl",
                             [{"ts": bm.now_iso(), "kind": "rework", "session_id": "s1"},
                              "null"])
            out = self._run("scorecard", v)
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("signals=1", out,
                      "the bare null signals row must be counted in the malformed "
                      "disclosure, not silently lower the rework count")

    def test_scorecard_reports_a_json_string_ratings_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1")])
            _write_telemetry(v, "ratings.jsonl",
                             [{"ts": bm.now_iso(), "score": 5, "attributed": True},
                              '"text"'])
            out = self._run("scorecard", v)
        self.assertIn("BROTHERMODE SCORECARD", out)
        self.assertIn("ratings=1", out)

    def test_migrate_survives_and_reports_a_bare_null_ledger_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1"), "null"])
            out = self._run("migrate", v)
        self.assertIn("1 malformed line(s)", out,
                      "migrate must name the non-record row it left out of the "
                      "rewritten file")
        self.assertIn("nothing was deleted", out)

    def test_dedup_survives_and_reports_a_bare_null_ledger_line(self):
        with tempfile.TemporaryDirectory() as v:
            _write_ledger(v, [_ledger_row("s1"), "null"])
            out = self._run("dedup", v)
        self.assertIn("1 malformed line(s) left in place", out,
                      "dedup must name the non-record row it read past")


class TestMeasuredCountsARealInputTokenValue(unittest.TestCase):
    """A2 finding 4 (2026-08-04). The comment above the `measured` line says
    it means 'has a real input-token field', but the test was `k in r`,
    which only proves the KEY is there. The same round taught fld() that a
    null is not a real number and left this sibling check eight lines below
    unchanged, so metric 2's stated 10-point target ('0 unmeasured') could
    be reached by rows carrying no usable number at all."""

    def test_null_and_string_input_tokens_are_not_counted_measured(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            _write_ledger(v, [
                _ledger_row("s1"),
                _ledger_row("s2", **{"gen_ai.usage.input_tokens": None}),
                _ledger_row("s3", **{"gen_ai.usage.input_tokens": "lots"}),
            ])
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout)
        self.assertIn("measured=1/3", r.stdout,
                      "only the row with a real numeric input-token value counts as "
                      "measured; a null or a string is the absence the comment above "
                      "that line already claims it reports")


class TestUnreadableTimestampsAreDisclosed(unittest.TestCase):
    """A2 finding 5 (2026-08-04). age_days returns None on any parse failure
    and every caller writes (age_days(...) or 99) or (... or 999), so a row
    whose ts is valid JSON but not a readable date is silently treated as
    ancient and drops out of every window, while still counting in the
    session total. The two numbers on the same printed line then disagree
    with nothing to explain it, and read_jsonl's malformed-line reporting
    cannot help because the row parses fine. The exclusion itself is
    unchanged; it is now disclosed."""

    def _run(self, cmd):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            _write_ledger(v, [_ledger_row("s1"), _ledger_row("s2", ts="yesterday")])
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"), cmd],
                               env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout)
        return r.stdout

    def test_scorecard_names_the_row_it_dropped_from_the_window(self):
        out = self._run("scorecard")
        self.assertIn("1 session(s) have a timestamp that could not be read", out,
                      "the scorecard must say how many rows its 7d window silently "
                      "excluded for an unreadable timestamp")
        self.assertIn("ledger: 2 sessions, 1 last 7d", out,
                      "the totals must still print, unchanged, alongside the note")

    def test_speed_names_the_row_it_dropped_from_both_windows(self):
        out = self._run("speed")
        self.assertIn("1 session(s) have a timestamp that could not be read", out,
                      "speed must say how many rows both windows silently excluded "
                      "for an unreadable timestamp")
        self.assertIn("last 7d ", out, "the speed table must still print")


class TestScorecardDisclosesFutureDatedSessions(unittest.TestCase):
    """A2 finding 6 (2026-08-04). The disclosure added for the future-dated
    row landed on cmd_speed, the command where that row is ABSENT, and
    nothing was added to cmd_scorecard, whose window (age <= 7, no lower
    bound) still COUNTS it: the output tokens of a session timestamped ahead
    of the clock are reported as spend from the last seven days with no
    comment. The two commands still disagreed about the same file, which is
    the exact condition the speed NOTE's own comment says it was written to
    prevent."""

    def test_a_future_dated_session_is_named_in_the_scorecard(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            future = (bm.datetime.datetime.now(bm.datetime.timezone.utc)
                      + bm.datetime.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            _write_ledger(v, [_ledger_row("s1"), _ledger_row("s2", ts=future)])
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout)
        self.assertIn("1 session(s) have a timestamp ahead of the clock", r.stdout,
                      "the scorecard counts a future-dated session in its last-7d "
                      "numbers and must say so, in the same words cmd_speed uses")
        self.assertIn("ledger: 2 sessions, 2 last 7d", r.stdout,
                      "the counting behavior itself is unchanged; only the silence is")


class TestUnusableNumericFieldsAreDisclosed(unittest.TestCase):
    """A2 finding 7 (2026-08-04). fld() substitutes its default for a
    present-but-non-numeric field, so the crash it replaced became a
    wrong-but-plausible number instead: two sessions, one of which really
    had unknown output tokens, were reported as one total with nothing said.
    The existing WARNING line scopes itself to lines that 'could not be
    parsed', so it stays silent here by construction. This file's own law at
    the top is 'Numbers are parsed or absent; nothing is invented', which
    makes a labelled absence the required outcome, not a silent zero."""

    def _run(self, cmd, rows):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            _write_ledger(v, rows)
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"), cmd],
                               env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout)
        return r.stdout

    def test_scorecard_names_the_substituted_fields_and_still_prints_totals(self):
        out = self._run("scorecard", [
            _ledger_row("s1"),
            _ledger_row("s2", **{"gen_ai.usage.output_tokens": None,
                                 "gen_ai.usage.input_tokens": None}),
        ])
        self.assertIn("2 numeric field(s) were present but could not be read as a number",
                      out, "the two substituted fields must be counted and named")
        self.assertIn("ledger: 2 sessions, 2 last 7d, 1k out last 7d", out,
                      "the totals must still print alongside the disclosure")
        self.assertIn("measured=1/2", out,
                      "the row whose fields were named unusable is the same row "
                      "'measured' excludes; the two numbers must agree")

    def test_speed_names_the_substituted_fields_and_still_prints_the_table(self):
        out = self._run("speed", [
            _ledger_row("s1"),
            _ledger_row("s2", duration_h=None, **{"gen_ai.usage.output_tokens": None}),
        ])
        self.assertIn("2 numeric field(s) were present but could not be read as a number",
                      out, "speed must disclose the substitutions in the same words "
                           "the scorecard uses")
        self.assertIn("last 7d ", out, "the speed table must still print")
        self.assertIn("prior 7d", out)


class TestScorecardAppliesTheWriteSideRatingRule(unittest.TestCase):
    """A2 finding 8 (2026-08-04). cmd_rate validates hard on the write side
    (int(), not float(), then 1 <= score <= 5, with a comment saying a
    fractional score 'would mean the system rated itself, not the founder'),
    and cmd_scorecard re-read the same file with only isinstance(x, (int,
    float)) and averaged whatever it found: 4.5, 99, -3 and a JSON `true`
    (an int in Python, and the exact type fld() deliberately excludes eight
    hundred lines earlier) all went straight into metric 4. The ledger this
    file calls the founder's own voice could carry a self-rating the write
    path was built to refuse."""

    def test_only_whole_scores_1_to_5_are_averaged_and_the_rest_are_named(self):
        with tempfile.TemporaryDirectory() as v, tempfile.TemporaryDirectory() as home:
            _write_ledger(v, [_ledger_row("s1")])
            _write_telemetry(v, "ratings.jsonl", [
                {"ts": bm.now_iso(), "score": s, "task": "t", "attributed": True}
                for s in (5, 4.5, 99, True, -3)
            ])
            r = subprocess.run([sys.executable, os.path.join(HERE, "bm_telemetry.py"),
                                "scorecard"], env=_telemetry_env(home, v), cwd=home,
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("swallowed error", r.stdout)
        self.assertIn("ratings=1 avg=5.00", r.stdout,
                      "only the score of 5 survives the rule the write side already "
                      "enforces; the average must be computed on that alone")
        self.assertIn("4 rating(s) carry a score the rate command itself refuses",
                      r.stdout,
                      "the four refused scores must be counted, not dropped silently")


class TestUserFacingFilesNeverTeachPathInference(unittest.TestCase):
    """An external review (2026-08-07) found every command file and the
    guided skill telling the model to work out the BrotherMode install path
    itself and prefix it onto tools/bm_*.py commands ("prefix that install
    path onto the command"), even though Claude Code already exports
    ${CLAUDE_PLUGIN_ROOT} inside skill and command content
    (https://code.claude.com/docs/en/plugins-reference, Environment
    variables: "Skill and agent content | Anywhere the placeholder
    appears"). The fix replaced every instance with
    python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_X.py" plus a one-line bare
    fallback for a clone install, stated in the same breath. This pin makes
    that ritual instruction impossible to reintroduce silently: a future
    edit that goes back to telling the model to infer and prefix the
    install path fails here first.

    Calibration: this test was made to fail once on purpose by pasting the
    old ritual sentence back into commands/brotherme-status.md, confirmed
    red, then the paste was removed and the test confirmed green again.

    H3 INVERSION, 2026-08-08 (V3-FREEZE-2026-08-07.md ruling H3, item 3;
    v3/architecture-refutation.md): the v3 rename adds nine canonical skill
    directories under skills/ and prepends a shim banner to every file in
    commands/, which shifts every line number this pin used to key on.
    `_user_facing_files()` now scans the widened v3 file set (commands/ plus
    every skills/*/SKILL.md, not only skills/brotherme/SKILL.md), and
    `known_bare` below is keyed by (path, matched invocation text) rather
    than (path, line number), so a banner or a reflow cannot silently
    invalidate an entry the way a line-numbered allowlist would. This is
    the rewrite ruling H3 named as the acceptance condition for closing
    item 3, done before any of the files it names moved or grew a banner."""

    ROOT = os.path.dirname(HERE)

    PATH_INFERENCE_RITUAL = re.compile(
        r"prefix\s+(?:that|the)\s+install\s+path"
        r"|work\s+out\s+the\s+install(?:ation)?\s+path"
        r"|relative\s+to\s+the\s+Brother\w*\s+install\s+folder",
        re.IGNORECASE)

    def _user_facing_files(self):
        out = sorted(glob.glob(os.path.join(self.ROOT, "commands", "*.md")))
        out.extend(sorted(glob.glob(
            os.path.join(self.ROOT, "skills", "*", "SKILL.md"))))
        return out

    def test_no_command_or_skill_file_teaches_the_model_to_infer_the_path(self):
        offenders = []
        for path in self._user_facing_files():
            self.assertTrue(os.path.isfile(path), "%s is missing" % path)
            text = _read(path)
            for i, line in enumerate(text.splitlines(), start=1):
                if self.PATH_INFERENCE_RITUAL.search(line):
                    offenders.append("%s:%d" % (os.path.relpath(path, self.ROOT), i))
        self.assertEqual(
            offenders, [],
            "these lines teach path inference again instead of naming "
            "${CLAUDE_PLUGIN_ROOT} directly with a stated clone-install "
            "fallback: %r" % offenders)

    def test_every_bm_tool_invocation_names_claude_plugin_root_or_a_fallback(self):
        """Every literal `python3 tools/bm_X.py` invocation in these files
        must sit beside a ${CLAUDE_PLUGIN_ROOT} form (either as the
        preferred command itself, or as the named bare fallback for a
        clone install next to one). A bare invocation with no
        ${CLAUDE_PLUGIN_ROOT} anywhere on its line is the same unresolved-
        path problem in a different shape.

        H3 INVERSION, 2026-08-08: `known_bare` used to be a set of exact
        "path:line" strings, which the class docstring's own H3 note
        already explains breaks the moment a file grows a banner or a
        section moves. It is now a set of (path, matched invocation text)
        pairs: the same invocation string can legitimately recur on more
        than one line in the same file (commands/brotherme-auto.md names
        `python3 tools/bm_controller.py` on three separate lines), and a
        set naturally covers all of them under one entry rather than
        needing one line-numbered entry per occurrence."""
        bare_invocation = re.compile(r"python3\s+\"?tools/bm_\w+\.py\"?")
        offenders = []  # (path, matched_text, line_number) for the message
        for path in self._user_facing_files():
            text = _read(path)
            for i, line in enumerate(text.splitlines(), start=1):
                m = bare_invocation.search(line)
                if m and "CLAUDE_PLUGIN_ROOT" not in line:
                    offenders.append((os.path.relpath(path, self.ROOT).replace(
                        os.sep, "/"), m.group(0), i))
        # Known bare invocations that predate the 2026-08-07 fix, or that
        # the v3 rename's commands/ shim banners and skills/ split carried
        # forward unchanged, and that are not part of the ritual this pin
        # guards (no install-path prose sits beside them either way): left
        # alone deliberately, tracked here BY CONTENT so a NEW bare
        # invocation still fails the test even if an old one shifts lines.
        known_bare = {
            ("commands/brotherme-auto-status.md", "python3 tools/bm_project.py"),
            ("commands/brotherme-auto.md", "python3 tools/bm_autonomy.py"),
            ("commands/brotherme-auto.md", "python3 tools/bm_controller.py"),
            ("commands/brotherme-stop.md", "python3 tools/bm_controller.py"),
            ("commands/brotherme-next.md", "python3 tools/bm_project.py"),
            ("commands/brotherme-status.md", "python3 tools/bm_project.py"),
            ("commands/brotherme-handback.md", "python3 tools/bm_view.py"),
            ("skills/brotherme/SKILL.md", "python3 tools/bm_view.py"),
            ("skills/brotherme/SKILL.md", "python3 tools/bm_docs.py"),
            # v3 skills/ carrying the same documented internal-adapter
            # exceptions their commands/ predecessors already had (H4:
            # these tools are not among the ten verbs the brothermode CLI
            # boundary owns, so they stay direct calls; see each file's
            # own v3 note).
            ("skills/auto/SKILL.md", "python3 tools/bm_autonomy.py"),
            ("skills/auto/SKILL.md", "python3 tools/bm_controller.py"),
            ("skills/stop/SKILL.md", "python3 tools/bm_controller.py"),
            ("skills/status/SKILL.md", "python3 tools/bm_project.py"),
            ("skills/handback/SKILL.md", "python3 tools/bm_view.py"),
        }
        new_offenders = [(p, t, i) for (p, t, i) in offenders
                         if (p, t) not in known_bare]
        self.assertEqual(
            new_offenders, [],
            "new bare tools/bm_*.py invocation(s) with no ${CLAUDE_PLUGIN_ROOT} "
            "anywhere on the line: %r (if this is a deliberate new bare spot "
            "outside the scope of the 2026-08-07 fix, add its (path, matched "
            "text) pair to known_bare with a reason)" % new_offenders)


class TestBmRepoScope(unittest.TestCase):
    """E76: per-repository hook scoping. tools/bm_repo_scope.py is the
    shared reader every hook calls at entry; this proves its own logic
    (find_repo_root, the config parse, the once-per-session notice) and
    then drives EVERY hook command hooks/hooks.json actually registers, as
    real subprocesses, so a hook added later is covered automatically
    rather than by a hand-picked sample."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._scratch = []
        subprocess.run(["git", "init", "-q", self.tmp], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for path in self._scratch:
            shutil.rmtree(path, ignore_errors=True)

    def _tools_farm(self, gate_module_text=None):
        """A symlink farm of the whole product root whose tools/ holds every
        real sibling except bm_repo_scope.py, which is written from
        `gate_module_text` (None means: symlink the real one too).

        A farm rather than a copy because the hooks resolve siblings from
        their own directory and the product root above it, so a hook run out
        of here behaves exactly as it does in place while reading OUR gate
        module. Returns the farm's tools/ directory."""
        product = os.path.dirname(HERE)
        base = tempfile.mkdtemp(prefix="bm-repo-scope-farm-")
        self._scratch.append(base)
        for name in os.listdir(product):
            if name != "tools":
                os.symlink(os.path.join(product, name),
                           os.path.join(base, name))
        tools = os.path.join(base, "tools")
        os.makedirs(tools)
        for name in os.listdir(HERE):
            if gate_module_text is not None and name == "bm_repo_scope.py":
                continue
            os.symlink(os.path.join(HERE, name), os.path.join(tools, name))
        if gate_module_text is not None:
            with io.open(os.path.join(tools, "bm_repo_scope.py"), "w",
                         encoding="utf-8") as f:
                f.write(gate_module_text)
        return tools

    def _write_config(self, text):
        d = os.path.join(self.tmp, ".brother")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "config"), "w", encoding="utf-8") as f:
            f.write(text)

    def _load(self):
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_under_test", os.path.join(HERE, "bm_repo_scope.py"))
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
        with contextlib.redirect_stderr(io.StringIO()):
            off = mod.hooks_off(cwd=self.tmp)
        self.assertFalse(off)

    def test_undecodable_bytes_default_active_with_one_diagnostic_line(self):
        d = os.path.join(self.tmp, ".brother")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config"), "wb") as f:
            f.write(b"\xff\xfe\x00garbage")
        mod = self._load()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            off = mod.hooks_off(cwd=self.tmp)
        self.assertFalse(off)
        self.assertIn("could not be read", err.getvalue())

    def test_notice_prints_once_per_session_across_two_calls(self):
        self._write_config("hooks: off\n")
        mod = self._load()
        first = io.StringIO()
        with contextlib.redirect_stderr(first):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "sX"}, cwd=self.tmp))
        second = io.StringIO()
        with contextlib.redirect_stderr(second):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "sX"}, cwd=self.tmp))
        self.assertIn("hooks are off", first.getvalue())
        self.assertEqual(second.getvalue(), "")

    def test_a_different_session_gets_its_own_notice(self):
        self._write_config("hooks: off\n")
        mod = self._load()
        with contextlib.redirect_stderr(io.StringIO()):
            mod.hooks_off(payload={"session_id": "sA"}, cwd=self.tmp)
        second = io.StringIO()
        with contextlib.redirect_stderr(second):
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
        retyped, the same expand-the-registry technique
        test_bm_consent.py uses for bm_hookchain.py's CHAINS table."""
        hooks_json = os.path.join(os.path.dirname(HERE), "hooks", "hooks.json")
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

    def _hook_payload(self, event, session_id):
        """The one payload shape the inert-when-off test and its positive
        control both send, written once so the two states provably differ
        only in whether .brother/config exists."""
        return json.dumps({
            "cwd": self.tmp, "session_id": session_id,
            "hook_event_name": event, "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(self.tmp, "x.py")},
            "transcript_path": os.path.join(self.tmp, "nonexistent.jsonl"),
            "reason": "test",
        })

    def _run_registered_hook(self, script, argv, payload):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, script)] + argv,
            input=payload, capture_output=True, text=True, cwd=self.tmp)

    def test_every_registered_hook_exits_zero_and_stays_inert_when_off(self):
        """The done_check's behavioral proof: every hook hooks/hooks.json
        wires up, run as a real subprocess against a repository with
        hooks turned off, exits 0 and prints nothing to stdout (no
        decision, no digest, no output at all), which means it stopped at
        the gate instead of doing its normal work.

        This assertion is only worth anything alongside
        test_at_least_one_registered_hook_is_loud_when_hooks_are_on below,
        which is its positive control: on a set of hooks that printed
        nothing in EITHER state, this test would stay green with the E76
        gate deleted outright.

        Narrowed 2026-09-04 (security review, Major): the WRITE GUARDS are
        exempt from the inertness claim on purpose, because a repository
        may not switch off the check standing between it and the person's
        disk. They are driven the other way, as guards, by
        test_the_write_guard_is_not_switched_off_by_the_repo_config below.
        The exemption is read from bm_repo_scope's own tuple rather than
        retyped, so a fourth guard added there is exempt here too."""
        self._write_config("hooks: off\n")
        guards = self._load().WRITE_GUARD_HOOKS
        commands = self._registered_hook_commands()
        self.assertGreater(
            len(commands), 0,
            "hooks.json parsed to zero commands; the regex or the fixture drifted")
        for i, (event, script, argv) in enumerate(commands):
            if script in guards:
                continue
            r = self._run_registered_hook(
                script, argv, self._hook_payload(event, "sess-%d" % i))
            self.assertEqual(
                r.returncode, 0,
                "%s %s (%s) did not exit 0 with hooks off: stderr=%r"
                % (script, argv, event, r.stderr))
            self.assertEqual(
                r.stdout, "",
                "%s %s (%s) printed to stdout with hooks off, so it did its "
                "normal work instead of stopping at the gate: %r"
                % (script, argv, event, r.stdout))

    def test_the_write_guard_is_not_switched_off_by_the_repo_config(self):
        """Security review 2026-09-04, Major. .brother/config is content
        that arrives WITH a repository, and "hooks: off" used to make every
        registered hook of both products exit at once, the write guards
        included, with one stderr line as the only trace. So a clone could
        switch off the check standing between it and the person's disk.
        Driven as a real subprocess, in enforced mode against a tree with
        no store, which is the fence's plainest refusal: with hooks off it
        must STILL deny, and it must say why it stayed on."""
        self._write_config("hooks: off\n")
        payload = json.dumps({
            "cwd": self.tmp, "session_id": "sess-guard",
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": os.path.join(self.tmp, "x.py")},
        })
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "bm_fence_hook.py")],
            input=payload, capture_output=True, text=True, cwd=self.tmp,
            env=dict(os.environ, BM_FENCE_MODE="enforced"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            r.stdout.strip(),
            "bm_fence_hook printed nothing with hooks off, so the "
            "repository's own config switched the write guard off: %r"
            % r.stderr)
        decision = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("does not switch off the write guards", r.stderr)

    def test_the_flag_refuses_to_report_off_and_the_other_hooks_still_stop(self):
        """The unit-level pair, so the mechanism is pinned and not only its
        one observed consequence: the SAME config, read twice, answers True
        for an ordinary hook and False for a write guard."""
        self._write_config("hooks: off\n")
        mod = self._load()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(
                mod.hooks_off(payload={"session_id": "g1"}, cwd=self.tmp))
            self.assertFalse(
                mod.hooks_off(payload={"session_id": "g1"}, cwd=self.tmp,
                              write_guard=True))

    def test_this_products_named_write_guard_really_passes_the_flag(self):
        """WRITE_GUARD_HOOKS is documentation unless something reads the
        call site: a hook renamed there, or a call site edited back to the
        plain call, fails here rather than silently reopening the hole.
        Only this product's own guard is checked from this file; the two
        BrotherSBE guards are checked in that product's own suite."""
        mod = self._load()
        self.assertIn("bm_fence_hook.py", mod.WRITE_GUARD_HOOKS)
        with io.open(os.path.join(HERE, "bm_fence_hook.py"),
                     encoding="utf-8") as f:
            source = f.read()
        self.assertIn("write_guard=True", source,
                      "bm_fence_hook.py is named a write guard but does not "
                      "pass write_guard=True, so .brother/config can switch "
                      "it off again")
    def test_at_least_one_registered_hook_is_loud_when_hooks_are_on(self):
        """THE POSITIVE CONTROL for the inert-when-off test above, and the
        only thing that makes its assertion mean anything.

        That test asserts stdout == "" for every registered hook with
        hooks off. Nothing in it distinguishes "the gate stopped the hook"
        from "this hook prints nothing anyway": the same file's
        test_absent_config_leaves_bm_session_cap_unaffected asserts an
        EMPTY stdout for bm_session_cap.py in the ON state too, so for at
        least that hook the off assertion is satisfied by silence that was
        never the gate's doing.

        So this runs the SAME payloads against the SAME repository with no
        .brother/config at all, then again with it, and refuses unless at
        least one hook's stdout actually differs between the two states.
        Delete the E76 gate and this fails, which is what the off test on
        its own could not do."""
        commands = self._registered_hook_commands()
        self.assertGreater(
            len(commands), 0,
            "hooks.json parsed to zero commands; the regex or the fixture drifted")
        # ON first: setUp leaves the repository with no .brother/config,
        # and writing one is what moves it to the off state below.
        on = [self._run_registered_hook(
                  script, argv, self._hook_payload(event, "on-%d" % i)).stdout
              for i, (event, script, argv) in enumerate(commands)]
        self._write_config("hooks: off\n")
        differed = []
        for i, (event, script, argv) in enumerate(commands):
            off = self._run_registered_hook(
                script, argv, self._hook_payload(event, "off-%d" % i)).stdout
            if on[i] != "" and off == "":
                differed.append("%s %s (%s)" % (script, argv, event))
        self.assertTrue(
            differed,
            "no registered hook printed anything to stdout with hooks ON "
            "that it stopped printing with hooks off, so the "
            "inert-when-off assertion above is vacuous: it would pass with "
            "the E76 gate deleted. Hooks tried: %r"
            % [(e, s, a) for (e, s, a) in commands])

    def test_an_unparseable_gate_module_degrades_to_active_and_exits_zero(self):
        """_load_bm_repo_scope's `except Exception: return None`, named by no
        test until now. It is a SILENT degrade to "hooks active", so the only
        way to see it is differentially: the SAME repository, with hooks
        turned off, run once against the real gate module and once against an
        unparseable one.

        Both halves matter. Exit 0 says a broken optional gate never takes
        the hook down with it; the non-empty stdout says what the fallback
        actually chose, which is the pre-E76 behaviour (do the normal work),
        never "stay inert because something went wrong"."""
        self._write_config("hooks: off\n")
        broken = "def hooks_off(  # deliberately unparseable\n"

        def run(tools):
            return subprocess.run(
                [sys.executable, os.path.join(tools, "bm_sessionstart.py")],
                input=json.dumps({"cwd": self.tmp,
                                  "session_id": "degrade-probe"}),
                capture_output=True, text=True, cwd=self.tmp)

        good = run(self._tools_farm())
        bad = run(self._tools_farm(gate_module_text=broken))
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(
            good.stdout, "",
            "the control: with a working gate module this hook is inert, so "
            "the farm itself is not what changes the outcome below")
        self.assertEqual(
            bad.returncode, 0,
            "an unparseable gate module must never take the hook down with "
            "it: stderr=%r" % bad.stderr)
        self.assertNotEqual(
            bad.stdout, "",
            "an unreadable gate module degrades to hooks ACTIVE, so this "
            "hook must do its normal work rather than stay silent")

    def test_absent_config_leaves_bm_session_cap_unaffected(self):
        """No .brother/config at all: bm_session_cap.py behaves exactly as
        it did before E76 (silent allow, exit 0, nothing on either
        stream)."""
        payload = json.dumps({"cwd": self.tmp, "session_id": "sess-plain"})
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "bm_session_cap.py")],
            input=payload, capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")

    def test_absent_config_leaves_bm_fence_hook_unaffected(self):
        """Same proof, second hook: whatever bm_fence_hook.py decides for a
        repository with no store (deny, in enforced mode, exit 0 either
        way), an ABSENT .brother/config must not change it. Compared
        against a baseline run rather than a fixed string, so this stays
        true regardless of BM_FENCE_MODE or the store-init message
        wording."""
        def run():
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "bm_fence_hook.py")],
                input=json.dumps({
                    "cwd": self.tmp, "session_id": "sess-plain2",
                    "tool_name": "Write",
                    "tool_input": {"file_path": os.path.join(self.tmp, "y.py")},
                }),
                capture_output=True, text=True, cwd=self.tmp)
        baseline = run()
        after = run()
        self.assertEqual(baseline.returncode, after.returncode)
        self.assertEqual(baseline.stdout, after.stdout)
        self.assertNotIn("hooks are off", after.stdout)

    # --- E50: the INSTALL-side default. The marker scripts/install.py writes
    # beside the settings file turns the absent-config case from ACTIVE into
    # INACTIVE, so a machine that installed Brother to try it on one
    # repository runs nothing anywhere else.

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
        """A scoped machine does not take the repository's own opt-out
        away: presence opts in, and the off line still turns it off."""
        self._with_config_dir(self._scoped_config_dir())
        mod = self._load()
        self._write_config("hooks: off\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(mod.hooks_off(payload={"session_id": "s52"},
                                          cwd=self.tmp))

    def test_no_marker_keeps_the_e76_default_so_a_clone_is_unchanged(self):
        """The marker is written by an installer only. A clone, a developer
        tree and this repository's own tests have none, so an absent
        .brother/config still means ACTIVE and nothing about E76 moved."""
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

    def test_the_inert_path_costs_one_stat_and_finishes_well_under_50_ms(self):
        """The row's own done_check: a hook invoked in a repository without
        the marker exits having read nothing, in under 50 ms. Timed in
        process on purpose, because that measures BROTHER's work; the
        subprocess figure the test below reports is dominated by a CPython
        interpreter start, which this row did not add and cannot remove.

        "Having read nothing" is asserted, not narrated: the repository's
        own .brother directory is never opened (there is none), and the
        gate leaves the repository tree byte for byte as it found it."""
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

    @staticmethod
    def _tree(root):
        found = set()
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                found.add(os.path.relpath(os.path.join(dirpath, name), root))
        return found

    def test_every_registered_hook_is_inert_in_an_unopted_repository(self):
        """E50's own proof, and the shape the row's done_check names: with a
        scoped install and NO .brother/config, every hook hooks/hooks.json
        registers, run as a real subprocess with its cwd inside an unrelated
        git repository, exits 0, prints nothing on either stream, and leaves
        the repository exactly as it found it."""
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self._scoped_config_dir()
        commands = self._registered_hook_commands()
        self.assertGreater(
            len(commands), 0,
            "hooks.json parsed to zero commands; the regex or the fixture drifted")
        before = self._tree(self.tmp)
        slowest = 0.0
        for i, (event, script, argv) in enumerate(commands):
            payload = json.dumps({
                "cwd": self.tmp, "session_id": "scoped-%d" % i,
                "hook_event_name": event, "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(self.tmp, "x.py")},
                "transcript_path": os.path.join(self.tmp, "nonexistent.jsonl"),
                "reason": "test",
            })
            started = time.time()
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, script)] + argv,
                input=payload, capture_output=True, text=True, cwd=self.tmp,
                env=env)
            slowest = max(slowest, time.time() - started)
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
        # Reported, never asserted. The row's done_check quotes a timed run
        # under 50 ms, and that budget is for the hook's own work, while this
        # measurement includes a whole CPython interpreter start on a shared
        # machine, so asserting on it would fail this suite for reasons that
        # have nothing to do with scoping.
        sys.stderr.write(
            "\n[E50] slowest inert hook invocation, interpreter startup "
            "included: %.1f ms over %d registered commands\n"
            % (slowest * 1000.0, len(commands)))


class TestBmInstallHookScope(unittest.TestCase):
    """E50, the install side: scripts/install.py's DEFAULT is a scoped
    install, and it says where hooks are active in one line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.dirname(HERE)
        self.config_dir = os.path.join(self.tmp, "claude")
        os.makedirs(self.config_dir)
        self.settings = os.path.join(self.config_dir, "settings.json")
        self.target = os.path.join(self.tmp, "installed")

    def _install(self, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(self.source, "scripts", "install.py"),
             "--target", self.target, "--settings", self.settings] + list(extra),
            capture_output=True, text=True)

    def test_the_default_install_scopes_itself_and_says_so_in_one_line(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        marker = os.path.join(self.config_dir, "brother-hook-scope")
        self.assertTrue(os.path.exists(marker),
                        "the default install left no scope marker, so its "
                        "hooks would run in every repository on the machine")
        with io.open(marker, encoding="utf-8") as f:
            self.assertIn("scope: repositories", f.read())
        lines = [l for l in r.stdout.splitlines() if l.startswith("hooks: act")]
        self.assertEqual(len(lines), 1,
                         "expected exactly one line naming where hooks are "
                         "active; got %r" % lines)
        self.assertIn("every other repository", lines[0])
        self.assertIn(".brother/config", lines[0],
                      "the line has to say how to add a repository")

    def test_repo_opts_one_repository_in_and_the_line_names_it(self):
        repo = os.path.join(self.tmp, "myproject")
        os.makedirs(repo)
        r = self._install("--repo", repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = os.path.join(repo, ".brother", "config")
        self.assertTrue(os.path.exists(cfg))
        with io.open(cfg, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "hooks: on")
        line = [l for l in r.stdout.splitlines() if l.startswith("hooks: act")]
        self.assertEqual(len(line), 1)
        self.assertIn(repo, line[0])
        self.assertIn("1 repository", line[0])

    def test_hooks_everywhere_writes_no_marker_and_removes_one(self):
        marker = os.path.join(self.config_dir, "brother-hook-scope")
        with io.open(marker, "w", encoding="utf-8") as f:
            f.write("scope: repositories\n")
        r = self._install("--hooks-everywhere")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker),
                         "--hooks-everywhere left a marker behind, so the "
                         "install it printed is not the install it made")
        self.assertIn("EVERY repository", r.stdout)

    def test_a_dry_run_writes_nothing(self):
        r = self._install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(self.config_dir, "brother-hook-scope")))

    def test_uninstall_removes_the_marker_it_installed(self):
        self.assertEqual(self._install().returncode, 0)
        marker = os.path.join(self.config_dir, "brother-hook-scope")
        self.assertTrue(os.path.exists(marker))
        r = subprocess.run(
            [sys.executable,
             os.path.join(self.source, "scripts", "uninstall.py"),
             "--target", self.target, "--settings", self.settings],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker),
                         "a marker outliving its install would scope a later, "
                         "unrelated Brother installation nobody asked to scope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
