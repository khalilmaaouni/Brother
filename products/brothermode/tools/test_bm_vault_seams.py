#!/usr/bin/env python3
"""Calibration for tools/bm_vault_seams.py and the LOUD/CLOSED contract it
backs (row P0-M, security finding 2026-09-06): every BM_VAULT_DISABLE_*
mutation seam is a fail-open test hook, so any consumer that reads
os.environ directly can silently run with a protection off and nothing
downstream can tell. This suite proves the shared reader itself, then
proves each consumer actually uses it: bm_vault.py's check prints a
stderr banner and marks every hit line, vault_recall_hook.py's own
lesson_states() records carry the marker, and bm_vault_intake.py refuses
to admit or capture into a real vault while a seam is active (the one
exemption: a vault whose path resolves under the system temp directory,
so the poisoning gauntlet can still prove the gate on its own throwaway
fixture).

Mirrors scripts/jbeq_decide.py's own mutation-seam contract, the sibling
half of this same finding (hub PR 396).

No em or en dashes anywhere in this file.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT_TOOL = os.path.join(HERE, "bm_vault.py")
INTAKE_TOOL = os.path.join(HERE, "bm_vault_intake.py")
HOOK_PATH = os.path.join(HERE, "vault_recall_hook.py")

sys.path.insert(0, HERE)
import bm_vault_seams as seams  # noqa: E402
import bm_vault_intake as intake  # noqa: E402

sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def load_hook():
    """A fresh import of vault_recall_hook.py, the same pattern
    scripts/gauntlet_memory_poisoning.py's own load_hook() and
    test_bm_vault_evidence_tier.py's own load_hook() use."""
    spec = importlib.util.spec_from_file_location(
        "vault_recall_hook_for_seams", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _EnvSavingCase(unittest.TestCase):
    """setUp/tearDown for every SEAM_VARS entry, the same save-and-restore
    shape scripts/test_gauntlet_memory_poisoning.py's own mutation test
    classes use, so one test setting a seam can never leak into the next."""

    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in seams.SEAM_VARS}

    def tearDown(self):
        for name, old in self._saved.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

    def _set(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class ActiveSeamsAndBanner(_EnvSavingCase):
    """bm_vault_seams.py's own two functions, direct, no subprocess: the
    module every consumer below reads instead of os.environ."""

    def test_no_seam_set_reads_empty(self):
        for name in seams.SEAM_VARS:
            self._set(name, None)
        self.assertEqual(seams.active_seams(), ())
        self.assertEqual(seams.banner(), "")

    def test_one_seam_set_reads_just_that_one(self):
        self._set("BM_VAULT_DISABLE_LIFECYCLE_GATE", "1")
        self.assertEqual(seams.active_seams(), ("BM_VAULT_DISABLE_LIFECYCLE_GATE",))
        self.assertEqual(
            seams.banner(),
            "MUTATION SEAM ACTIVE (vault protections disabled: "
            "BM_VAULT_DISABLE_LIFECYCLE_GATE)")

    def test_several_seams_set_read_sorted_and_joined(self):
        self._set("BM_VAULT_DISABLE_LIFECYCLE_GATE", "1")
        self._set("BM_VAULT_DISABLE_ANCHOR_CHECK", "1")
        self.assertEqual(
            seams.active_seams(),
            ("BM_VAULT_DISABLE_ANCHOR_CHECK", "BM_VAULT_DISABLE_LIFECYCLE_GATE"))
        self.assertIn("BM_VAULT_DISABLE_ANCHOR_CHECK", seams.banner())
        self.assertIn("BM_VAULT_DISABLE_LIFECYCLE_GATE", seams.banner())

    def test_an_empty_string_value_does_not_count_as_set(self):
        # The same truthiness every existing os.environ.get(...) call site
        # this module replaces already used: os.environ["X"] = "" is falsy.
        self._set("BM_VAULT_DISABLE_CREDENTIAL_GATE", "")
        self.assertEqual(seams.active_seams(), ())

    def test_banner_accepts_a_precomputed_seam_tuple(self):
        self.assertEqual(
            seams.banner(("BM_VAULT_DISABLE_DENYLIST_GATE",)),
            "MUTATION SEAM ACTIVE (vault protections disabled: "
            "BM_VAULT_DISABLE_DENYLIST_GATE)")


class BmVaultCheckIsLoud(_EnvSavingCase):
    """bm_vault.py check: one stderr banner per run while a seam is
    active, and every hit line (WITHHELD or served) carries the same
    marker (row P0-M's own "wrap every result" posture, matching
    scripts/jbeq_decide.py's decide())."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-seams-check-")
        os.makedirs(os.path.join(self.tmp, ".claude"))
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "10-Lessons"))
        note_path = os.path.join(self.vault, "10-Lessons", "widget-fact.md")
        with open(note_path, "w", encoding="utf-8") as fh:
            fh.write(
                "---\napplies_to: widget.py\n---\n\n"
                "# widget fact\n\nwidget.py does one thing. "
                "TEST-FIXTURE-BM-VAULT-SEAMS-DO-NOT-COPY.\n")
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BROTHERMODE_ROOT"] = self.tmp
        subprocess.run([sys.executable, VAULT_TOOL, "index", "--vault", self.vault],
                       env=self.env, cwd=self.tmp,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _check(self, env_overrides):
        env = dict(self.env)
        env.update(env_overrides)
        p = subprocess.run(
            [sys.executable, VAULT_TOOL, "check", "--paths", "widget.py", "--limit", "5"],
            env=env, cwd=self.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return (p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))

    def test_no_seam_prints_no_banner_and_no_marker(self):
        out, err = self._check({})
        self.assertNotIn("MUTATION SEAM ACTIVE", err)
        self.assertNotIn("MUTATION SEAM ACTIVE", out)

    def test_one_seam_prints_the_stderr_banner_once(self):
        out, err = self._check({"BM_VAULT_DISABLE_LIFECYCLE_GATE": "1"})
        self.assertEqual(
            err.count("MUTATION SEAM ACTIVE (vault protections disabled: "
                      "BM_VAULT_DISABLE_LIFECYCLE_GATE)"),
            1, err)

    def test_the_hit_line_itself_carries_the_marker(self):
        out, _err = self._check({"BM_VAULT_DISABLE_LIFECYCLE_GATE": "1"})
        # The exact kind/source pair varies by note; assert the marker text
        # appears on the SAME line as the title, not merely somewhere in
        # the output.
        title_lines = [ln for ln in out.split("\n") if "widget fact" in ln]
        self.assertTrue(title_lines, out)
        self.assertIn("MUTATION SEAM ACTIVE", title_lines[0])


class VaultRecallHookRecordsCarryTheMarker(_EnvSavingCase):
    """lesson_states() (vault_recall_hook.py): every record it returns
    carries a "mutation" field while a seam is active, the same
    "wrap every result" posture bm_vault.py's own hit lines use, never a
    fourth MEMORY_STATES value (receipt_door.py owns that vocabulary)."""

    def setUp(self):
        super().setUp()
        self.hook = load_hook()
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-seams-hook-")
        with open(os.path.join(self.tmp, "widget.py"), "w", encoding="utf-8") as fh:
            fh.write("def widget():\n    pass\n")
        self.note_path = os.path.join(self.tmp, "widget-fact.md")
        with open(self.note_path, "w", encoding="utf-8") as fh:
            fh.write("---\napplies_to:\n  - widget.py\n---\n\nwidget does a thing.\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _out(self):
        return ("\n  widget fact  [lesson, harvest]\n"
               "    %s\n" % self.note_path)

    def test_no_seam_no_mutation_key(self):
        for name in seams.SEAM_VARS:
            self._set(name, None)
        records, _out2 = self.hook.lesson_states(self._out(), self.tmp)
        self.assertEqual(len(records), 1)
        self.assertNotIn("mutation", records[0])

    def test_one_seam_active_every_record_carries_it(self):
        self._set("BM_VAULT_DISABLE_ANCHOR_CHECK", "1")
        records, _out2 = self.hook.lesson_states(self._out(), self.tmp)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["mutation"],
                         {"disabled": ["BM_VAULT_DISABLE_ANCHOR_CHECK"]})

    def test_withheld_blocks_are_never_given_a_record_or_a_mutation_key(self):
        # Row-shape untouched by this change: a WITHHELD block still gets
        # no record at all, seam active or not (lesson_states' own,
        # pre-existing contract).
        self._set("BM_VAULT_DISABLE_ANCHOR_CHECK", "1")
        withheld_out = "\n  WITHHELD (stale)  widget fact  [lesson, harvest]\n    reason: gone\n"
        records, _out2 = self.hook.lesson_states(withheld_out, self.tmp)
        self.assertEqual(records, [])


class BmVaultIntakeRefusesAdmissionUnderASeam(_EnvSavingCase):
    """bm_vault_intake.py: admit and capture both refuse outright, before
    a single byte is written, while ANY seam is active and the target
    vault does not resolve under the system temp directory; a vault that
    DOES resolve there (the poisoning gauntlet's own throwaway fixture) is
    let through so the gate can still be proven mutated.

    tempfile.mkdtemp() always allocates under the REAL system temp
    directory, so a fixture vault nested under one mkdtemp() result is
    itself always "under system temp" and could never stand in for a real
    one. What counts as "the system temp directory" is controlled
    explicitly instead: mock.patch over tempfile.gettempdir() for the
    direct, in-process calls, and the TMPDIR environment variable for the
    subprocess ones (a fresh interpreter reads it, uncached, unlike this
    process's own tempfile module which caches gettempdir()'s first
    answer)."""

    def setUp(self):
        super().setUp()
        # Two SIBLING directories, both real OS temp dirs, but neither
        # nested inside the other: one plays "the system temp directory"
        # for this test, the other plays an ordinary real vault location
        # that must never resolve under it.
        self.fake_system_temp = tempfile.mkdtemp(prefix="bm-vault-seams-faketemp-")
        self.real_vault_root = tempfile.mkdtemp(prefix="bm-vault-seams-realvault-")
        self.real_vault = os.path.join(self.real_vault_root, "vault")
        os.makedirs(self.real_vault)
        self.throwaway_vault = os.path.join(self.fake_system_temp, "vault")
        os.makedirs(self.throwaway_vault)
        self.src = os.path.join(self.real_vault_root, "source.txt")
        with open(self.src, "w", encoding="utf-8") as fh:
            fh.write("an ordinary line, nothing dirty here\n")

    def tearDown(self):
        shutil.rmtree(self.fake_system_temp, ignore_errors=True)
        shutil.rmtree(self.real_vault_root, ignore_errors=True)
        super().tearDown()

    def _patched_gettempdir(self):
        return unittest.mock.patch.object(
            intake.tempfile, "gettempdir", return_value=self.fake_system_temp)

    def test_direct_no_seam_no_refusal_even_outside_temp(self):
        for name in seams.SEAM_VARS:
            self._set(name, None)
        with self._patched_gettempdir():
            self.assertIsNone(intake._admission_seam_refusal(self.real_vault))

    def test_direct_seam_active_refuses_a_real_vault(self):
        self._set("BM_VAULT_DISABLE_CREDENTIAL_GATE", "1")
        with self._patched_gettempdir():
            refusal = intake._admission_seam_refusal(self.real_vault)
        self.assertIsNotNone(refusal)
        self.assertIn("MUTATION SEAM ACTIVE", refusal)
        self.assertIn("BM_VAULT_DISABLE_CREDENTIAL_GATE", refusal)

    def test_direct_seam_active_allows_a_vault_under_system_temp(self):
        self._set("BM_VAULT_DISABLE_CREDENTIAL_GATE", "1")
        with self._patched_gettempdir():
            self.assertIsNone(intake._admission_seam_refusal(self.throwaway_vault))

    def test_subprocess_admit_refuses_into_a_real_vault_under_a_seam(self):
        env = dict(os.environ)
        env["BM_VAULT_DISABLE_CREDENTIAL_GATE"] = "1"
        env["TMPDIR"] = self.fake_system_temp
        p = subprocess.run(
            [sys.executable, INTAKE_TOOL, "admit", "--vault", self.real_vault,
             "--source", "test", "--by", "tester", self.src],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 1)
        stderr = p.stderr.decode("utf-8", "replace")
        self.assertIn("REFUSED", stderr)
        self.assertIn("MUTATION SEAM ACTIVE", stderr)
        self.assertFalse(os.path.isdir(os.path.join(self.real_vault, "00-Inbox")))

    def test_subprocess_admit_lands_into_a_throwaway_vault_under_a_seam(self):
        env = dict(os.environ)
        env["BM_VAULT_DISABLE_CREDENTIAL_GATE"] = "1"
        env["TMPDIR"] = self.fake_system_temp
        p = subprocess.run(
            [sys.executable, INTAKE_TOOL, "admit", "--vault", self.throwaway_vault,
             "--source", "test", "--by", "tester", self.src],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertTrue(os.listdir(os.path.join(self.throwaway_vault, "00-Inbox")))

    def test_subprocess_capture_refuses_into_a_real_vault_under_a_seam(self):
        env = dict(os.environ)
        env["BM_VAULT_DISABLE_DENYLIST_GATE"] = "1"
        env["TMPDIR"] = self.fake_system_temp
        p = subprocess.run(
            [sys.executable, INTAKE_TOOL, "capture", "--vault", self.real_vault,
             "--by", "tester", "a captured thought"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 1)
        self.assertIn("MUTATION SEAM ACTIVE",
                      p.stderr.decode("utf-8", "replace"))
        self.assertFalse(os.path.isdir(os.path.join(self.real_vault, "00-Inbox")))


def demo():
    """ponytail: the smallest end-to-end check this suite's own logic
    needs, runnable without unittest's discovery machinery."""
    assert seams.active_seams() == ()
    os.environ["BM_VAULT_DISABLE_LIFECYCLE_GATE"] = "1"
    try:
        assert seams.active_seams() == ("BM_VAULT_DISABLE_LIFECYCLE_GATE",)
    finally:
        del os.environ["BM_VAULT_DISABLE_LIFECYCLE_GATE"]
    print("test_bm_vault_seams: demo OK")


if __name__ == "__main__":
    unittest.main()
