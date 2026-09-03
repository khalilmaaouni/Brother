"""What the post-run scope audit must keep true.

This is the founder's detect-and-quarantine answer, chosen over declaring every
hidden surface in advance. The reason that answer is allowed to be the cheap one
is that this estate commits at every checkpoint, so an undeclared write is one
revert away. The tests below hold the parts of that bargain the code owes.

Every case runs against a REAL git repository rather than a fake diff, because
the thing being audited is what git says changed, and a fake would only prove the
comparison and never the reading.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scope_audit as A  # noqa: E402


class Repo(object):
    """A throwaway repository with a two commit history."""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        self.write("declared.py", "x = 1\n")
        self.write("other.py", "y = 1\n")
        self.write("pkg/inner.py", "z = 1\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")
        self.base = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.dir,
                              capture_output=True, text=True)

    def write(self, rel, body):
        full = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body)

    def commit(self, message="change"):
        self._git("add", "-A")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TheAuditCatchesWhatNobodyDeclared(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_writing_only_inside_the_declaration_is_CLEAN(self):
        self.repo.write("declared.py", "x = 2\n")
        head = self.repo.commit()
        verdict, _ = A.audit({"unit_id": "U", "write_scope": ["declared.py"]},
                             self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.CLEAN)

    def test_writing_OUTSIDE_it_is_QUARANTINE_and_names_the_path(self):
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        verdict, detail = A.audit({"unit_id": "U", "write_scope": ["declared.py"]},
                                  self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.QUARANTINE)
        self.assertEqual(detail["undeclared"], ["other.py"])

    def test_a_directory_declaration_covers_a_file_under_it(self):
        self.repo.write("pkg/inner.py", "z = 2\n")
        head = self.repo.commit()
        verdict, _ = A.audit({"unit_id": "U", "write_scope": ["pkg/"]},
                             self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.CLEAN)

    def test_the_repository_ROOT_covers_everything(self):
        """Found by driving the case rather than reading the function: a unit
        declaring the root was quarantined for writing inside it, because
        'other.py' does not start with './'."""
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        for root in (".", "./", " . "):
            verdict, _ = A.audit({"unit_id": "U", "write_scope": [root]},
                                 self.repo.base, head, cwd=self.repo.dir)
            self.assertEqual(verdict, A.CLEAN, repr(root))

    def test_several_undeclared_paths_are_all_reported_not_just_the_first(self):
        self.repo.write("other.py", "y = 2\n")
        self.repo.write("pkg/inner.py", "z = 2\n")
        head = self.repo.commit()
        verdict, detail = A.audit({"unit_id": "U", "write_scope": ["declared.py"]},
                                  self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.QUARANTINE)
        self.assertEqual(detail["undeclared"], ["other.py", "pkg/inner.py"])


class ReadOnlyIsNotTheSameAsUndeclared(unittest.TestCase):
    """Absent is not read-only, and the two must not produce one verdict."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_a_unit_that_declared_READ_ONLY_and_wrote_is_QUARANTINE(self):
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        verdict, _ = A.audit({"unit_id": "U", "write_scope": []},
                             self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.QUARANTINE)

    def test_a_unit_with_NO_write_scope_key_is_NO_DATA_not_quarantine(self):
        """Nothing to compare against is a different fact from writing outside a
        declaration, and it says so: an undeclared unit should never have been
        dispatched in the first place."""
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        verdict, detail = A.audit({"unit_id": "U"}, self.repo.base, head,
                                  cwd=self.repo.dir)
        self.assertEqual(verdict, A.NO_DATA)
        self.assertIn("never have been dispatched", detail["reason"])

    def test_a_read_only_unit_that_wrote_NOTHING_is_CLEAN(self):
        head = self.repo.commit("empty")
        verdict, _ = A.audit({"unit_id": "U", "write_scope": []},
                             self.repo.base, self.repo.base, cwd=self.repo.dir)
        self.assertEqual(verdict, A.CLEAN)


class NoDataIsNeverAPass(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_an_unresolvable_ref_is_NO_DATA_and_never_CLEAN(self):
        """A diff git could not compute must not read as nothing changed."""
        verdict, detail = A.audit({"unit_id": "U", "write_scope": ["x"]},
                                  "nosuchref", cwd=self.repo.dir)
        self.assertEqual(verdict, A.NO_DATA)
        self.assertIn("nosuchref", detail["reason"])

    def test_the_three_exit_codes_are_three_distinct_values(self):
        self.assertEqual(len({A.EXIT_CLEAN, A.EXIT_QUARANTINE, A.EXIT_NO_DATA}), 3)

    def test_quarantine_does_not_exit_zero(self):
        """A wrapper reading only the exit code must not see a quarantine as a
        pass."""
        self.assertNotEqual(A.EXIT_QUARANTINE, A.EXIT_CLEAN)


class ItReadsGitRatherThanTheWorkersOwnAccount(unittest.TestCase):
    """A worker reporting its own artifacts is the thing being audited, so
    believing it would make the audit circular."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_a_worker_claiming_it_touched_nothing_is_still_caught(self):
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        unit = {"unit_id": "U", "write_scope": ["declared.py"],
                "artifacts": [], "worker_claim": "I changed nothing"}
        verdict, _ = A.audit(unit, self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.QUARANTINE)


class BytecodeIsNeverAWrite(unittest.TestCase):
    """A worker that RUNS the tests it wrote leaves __pycache__ behind, which
    is the interpreter's side effect and not content. Counting it quarantined
    a correct unit on the first live product-path run (2026-08-30): the
    false-refusal class. Real undeclared files must still quarantine."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_pycache_alone_is_clean(self):
        self.repo.write("declared.py", "x = 1\n")
        self.repo.write("tests/__pycache__/t.cpython-313.pyc", "\x00")
        head = self.repo.commit()
        unit = {"unit_id": "U", "write_scope": ["declared.py"]}
        verdict, _ = A.audit(unit, self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.CLEAN)

    def test_a_real_undeclared_file_beside_pycache_still_quarantines(self):
        self.repo.write("declared.py", "x = 1\n")
        self.repo.write("tests/__pycache__/t.cpython-313.pyc", "\x00")
        self.repo.write("sneaky.py", "z = 3\n")
        head = self.repo.commit()
        unit = {"unit_id": "U", "write_scope": ["declared.py"]}
        verdict, detail = A.audit(unit, self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, A.QUARANTINE)
        self.assertIn("sneaky.py", str(detail))
        self.assertNotIn("__pycache__", str(detail))


if __name__ == "__main__":
    unittest.main()
