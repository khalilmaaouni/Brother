"""KEY1.a harness: the write notice says "Vault saved" only for a note whose
destination is inside the configured vault.

Contract (plan, KEY1.a): "The notice says Vault saved only when the note's
destination is inside the configured vault. A lesson kept in the run
directory prints that it is kept in this run and is not in the Vault, with
its path. With no vault configured the words Vault saved never appear."

Entry point under test: brother_run.build_report, the real seam that drafts
a lesson for a refused unit and prints the notice. Not the print helper.

Every case isolates ONE condition. The configured vault is bound through
BOTH env names the estate reads (BROTHERMODE_VAULT_ROOT, which brother_run
uses today, and BROTHERMODE_VAULT, the name the rest of the estate uses), so
the harness does not decide which one the fix reads. Hermetic: stdlib
unittest, a tempdir for the run and the vault, no network, no real vault.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import journal  # noqa: E402

VAULT_ENVS = ("BROTHERMODE_VAULT_ROOT", "BROTHERMODE_VAULT")


class VaultSavedOnlyForVault(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="key1a-"))
        self.run_dir = os.path.join(self.tmp, "run1")
        os.makedirs(self.run_dir)
        self.saved = {k: os.environ.get(k) for k in VAULT_ENVS + (
            journal.RUN_DIR_ENV_VAR, "BROTHERMODE_RECURRENCE_DB")}
        for k in VAULT_ENVS:
            os.environ.pop(k, None)
        os.environ[journal.RUN_DIR_ENV_VAR] = self.run_dir
        os.environ["BROTHERMODE_RECURRENCE_DB"] = os.path.join(
            self.tmp, "recurrence.sqlite3")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bind_vault(self, path):
        os.makedirs(path, exist_ok=True)
        for k in VAULT_ENVS:
            os.environ[k] = path

    def _run_refused_unit(self):
        """One refused unit, so one lesson lands in <run_dir>/lessons/.
        Returns (stdout, lesson_path)."""
        rec = {"outcome": "key1a", "work_id": "w-key1a", "rows": [
            {"id": "U1", "objective": "the one that fails",
             "done_check": "false",
             "integration_refused": "the check exited 1"}]}
        claims = {"U1": {"state": "failed"}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _br.build_report(rec, claims, "abc123", "def456", changed=[])
        lessons = os.path.join(self.run_dir, "lessons")
        names = sorted(os.listdir(lessons)) if os.path.isdir(lessons) else []
        self.assertEqual(len(names), 1,
                         "fixture broke: want one lesson in %s, got %r"
                         % (lessons, names))
        return buf.getvalue(), os.path.join(lessons, names[0])

    @staticmethod
    def _saved_lines(out):
        return [ln for ln in out.splitlines() if "Vault saved" in ln]

    @staticmethod
    def _kept_lines(out):
        return [ln for ln in out.splitlines()
                if "kept in this run" in ln.lower()]

    # RED today: the lesson lives in the run directory, no vault is bound,
    # and the old line still claims it was saved to the Vault.
    def test_no_vault_configured_never_prints_vault_saved(self):
        out, _ = self._run_refused_unit()
        self.assertEqual(self._saved_lines(out), [],
                         "no vault is configured yet the notice says "
                         "Vault saved:\n" + out)

    # RED today: a vault is configured, the lesson sits in the run dir
    # (outside the vault), and the notice still says Vault saved.
    def test_lesson_in_run_dir_outside_configured_vault_is_not_vault_saved(self):
        self._bind_vault(os.path.join(self.tmp, "somevault"))
        out, _ = self._run_refused_unit()
        self.assertEqual(self._saved_lines(out), [],
                         "lesson is in the run dir, outside the configured "
                         "vault, yet the notice says Vault saved:\n" + out)

    # RED today: the honest line does not exist yet.
    def test_run_dir_lesson_prints_kept_in_this_run_not_in_the_vault(self):
        self._bind_vault(os.path.join(self.tmp, "somevault"))
        out, _ = self._run_refused_unit()
        kept = self._kept_lines(out)
        self.assertEqual(len(kept), 1, "want exactly one 'kept in this run' "
                         "line:\n" + out)
        self.assertIn("not in the vault", kept[0].lower(), kept[0])

    # RED today: the honest line must carry the path of the lesson.
    def test_kept_line_names_the_lesson_path(self):
        self._bind_vault(os.path.join(self.tmp, "somevault"))
        out, lesson = self._run_refused_unit()
        kept = self._kept_lines(out)
        self.assertEqual(len(kept), 1, out)
        rel = os.path.relpath(lesson, self.run_dir)
        self.assertTrue(rel in kept[0] or lesson in kept[0],
                        "kept line names no path (%s or %s): %s"
                        % (rel, lesson, kept[0]))

    # RED today: with no vault at all the run-dir lesson is still kept in
    # this run and the notice must still say so (not stay silent).
    def test_no_vault_configured_still_prints_kept_in_this_run(self):
        out, _ = self._run_refused_unit()
        kept = self._kept_lines(out)
        self.assertEqual(len(kept), 1, "want exactly one 'kept in this run' "
                         "line:\n" + out)
        self.assertIn("not in the vault", kept[0].lower(), kept[0])

    # GREEN control (passes today): a lesson that really is inside the
    # configured vault (the run dir sits under the vault root) keeps the
    # Vault saved line, so a fix cannot pass by refusing to ever print it.
    def test_lesson_inside_configured_vault_still_prints_vault_saved(self):
        self._bind_vault(self.tmp)  # run_dir is tmp/run1, inside the vault
        out, lesson = self._run_refused_unit()
        saved = self._saved_lines(out)
        self.assertEqual(len(saved), 1, out)
        self.assertTrue(saved[0].startswith("Vault saved: U1 -> "), saved[0])
        self.assertEqual(self._kept_lines(out), [],
                         "an in-vault note must not also claim it was kept "
                         "outside the vault:\n" + out)

    # GREEN control (passes today): a failed draft prints neither line.
    def test_failed_draft_prints_neither_line(self):
        with open(os.path.join(self.run_dir, "lessons"), "w") as fh:
            fh.write("a file where the lessons directory should be")
        rec = {"outcome": "key1a", "work_id": "w-key1a", "rows": [
            {"id": "U1", "objective": "x", "done_check": "false",
             "integration_refused": "the check exited 1"}]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _br.build_report(rec, {"U1": {"state": "failed"}}, "a", "b",
                             changed=[])
        out = buf.getvalue()
        self.assertEqual(self._saved_lines(out), [], out)
        self.assertEqual(self._kept_lines(out), [], out)


if __name__ == "__main__":
    unittest.main()
