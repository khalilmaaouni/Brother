"""TOKEN-04 calibration: an invented call is caught, a real one passes, and
an unverifiable draft is never called verified.

THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a checker that silently ignores
what it cannot parse or import, and prints VERIFIED over a draft calling a
function nobody wrote. Every case below aims at that.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deepseek_draft as D  # noqa: E402

REAL = '''
def existing(a, b=2):
    return a + b


class Thing:
    pass
'''


class CheckDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = self.tmp.name
        with open(os.path.join(self.tree, "real_module.py"), "w") as fh:
            fh.write(REAL)

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, source):
        return D.check_draft(source, self.tree)[0]

    def checked(self, source):
        return D.check_draft(source, self.tree)[1]

    def test_a_draft_referencing_nothing_is_no_data_not_verified(self):
        """The trap this estate has on record: a population of nothing
        composing into a PASS."""
        self.assertEqual(self.checked("x = 1\nprint(x)\n"), 0)
        self.assertGreater(self.checked("import real_module\nreal_module.existing(1)\n"), 0)

    def test_a_real_call_verifies(self):
        self.assertEqual(self.problems("import real_module\nreal_module.existing(1)\n"), [])

    def test_an_invented_function_is_caught(self):
        found = self.problems("import real_module\nreal_module.invented(1)\n")
        self.assertEqual([k for k, _ in found], ["invented"])
        self.assertIn("real_module.invented", found[0][1])

    def test_an_invented_from_import_is_caught(self):
        found = self.problems("from real_module import nope\n")
        self.assertEqual([k for k, _ in found], ["invented"])

    def test_a_real_from_import_called_wrongly_is_caught(self):
        found = self.problems("from real_module import existing\nexisting()\n")
        self.assertEqual([k for k, _ in found], ["mis-called"])

    def test_too_many_arguments_is_caught(self):
        found = self.problems("import real_module\nreal_module.existing(1, 2, 3)\n")
        self.assertEqual([k for k, _ in found], ["mis-called"])

    def test_an_alias_is_followed(self):
        found = self.problems("import real_module as rm\nrm.invented()\n")
        self.assertEqual([k for k, _ in found], ["invented"])

    def test_standard_library_and_locals_are_not_judged(self):
        self.assertEqual(self.problems("import json\njson.dumps({}, indent=2)\n"), [])
        self.assertEqual(self.problems("x = 1\nprint(x)\n"), [])

    def test_star_args_are_not_judged_rather_than_guessed(self):
        self.assertEqual(self.problems("import real_module\nargs=[1]\nreal_module.existing(*args)\n"), [])

    def test_a_draft_that_does_not_parse_is_unverifiable(self):
        with self.assertRaises(D.Unverifiable):
            self.problems("def (:\n")

    def test_a_tree_module_that_cannot_import_is_unverifiable(self):
        with open(os.path.join(self.tree, "broken.py"), "w") as fh:
            fh.write("raise RuntimeError('boom')\n")
        with self.assertRaises(D.Unverifiable):
            self.problems("import broken\nbroken.anything()\n")

    def test_a_missing_tree_is_unverifiable(self):
        with self.assertRaises(D.Unverifiable):
            D.check_draft("x = 1\n", os.path.join(self.tree, "gone"))


class ExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = self.tmp.name
        with open(os.path.join(self.tree, "real_module.py"), "w") as fh:
            fh.write(REAL)
        self.draft = os.path.join(self.tree, "draft.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.draft, "w") as fh:
            fh.write(text)
        return ["--draft", self.draft, "--tree", self.tree]

    def test_verified_refused_and_no_data(self):
        self.assertEqual(D.main(self.write("import real_module\nreal_module.existing(1)\n")), 0)
        self.assertEqual(D.main(self.write("import real_module\nreal_module.invented()\n")), 1)
        self.assertEqual(D.main(self.write("def (:\n")), 2)
        self.assertEqual(D.main(self.write("x = 1\n")), 2, "a draft checking nothing is NO-DATA")
        self.assertEqual(D.main(["--draft", os.path.join(self.tree, "none.py"),
                                 "--tree", self.tree]), 2)


if __name__ == "__main__":
    unittest.main()
