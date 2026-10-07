#!/usr/bin/env python3
"""FX-13.3 of the plan lint: the council verdict rules (COUNCIL, COUNCIL-ABSENT) and the role content rule
(ROLE-CONTENT). Every fixture is built in a temp folder with HOME pointed at it; the real council file, prompts and
roles file are never read, so the suite runs the same on the export tree with an empty HOME."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import plan_lint  # noqa: E402  (the module under test, beside this file)


SPEC = """# U: one unit

## U.1 one section

Done check:

```
python3 -B scripts/test_a.py
```
"""
OLD_SPEC = """# U: one unit

## U.1 the section as it read before its revision
"""
PROMPT = """You hold the security seat.

THE SPECIFICATION:
%s

THE REAL FILES IT NAMES (excerpts):
none
"""
CLAIMS = ["the gate fails open on a missing file", "no test can go red"]
SITES = {
    "scripts/loop/unit_runner.py": 'job = {"id": "U.1", "sensitivity": "public"}\n',
    "scripts/loop/probe_wave.py": 'import model_router as R\njob = dict(unit="U.1")\njob["sensitivity"] = R.PUBLIC\n',
    "scripts/loop/check_wave.py": ('import model_call\nimport model_router as R\n'
                                   'model_call.call_one("m", "p", "grade", R.PUBLIC, timeout=60)\n'),
    "scripts/loop/finish_run.py": 'import model_call\nmodel_call.call_one("m", "prompt", "build", "public", timeout=900)\n',
}
CLASSES = {"worker": "public", "adversary": "public", "checker": "public", "finisher": "private",
           "orchestrator": "private", "documenter": "private"}


def open_unit(state="OPEN"):
    return {"id": "U", "state": state, "done_check": "python3 -B scripts/test_a.py", "spec": "spec.md",
            "sub_units": ["U.1"], "evidence": ""}


def entry(state, claims=()):
    return {"state": state, "seats": {}, "blockers": [{"claim": c, "proof": "", "expect": ""} for c in claims],
            "majors": 0, "min_safety": 5}


def sev(found):
    return [(f["rule"], f["severity"]) for f in found]


class _Home(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fx133-")
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = {k: os.environ.get(k) for k in ("HOME", "BROTHER_LOOP_ROLES")}
        self.addCleanup(self._restore, saved)
        os.environ["HOME"] = self.root
        os.environ.pop("BROTHER_LOOP_ROLES", None)

    @staticmethod
    def _restore(saved):
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        return path


class RulesTest(_Home):
    """COUNCIL and COUNCIL-ABSENT, through lint_unit with a council mapping or a council file path."""

    def setUp(self):
        _Home.setUp(self)
        self.write("spec.md", SPEC)
        self.council_dir = os.path.join(self.root, "spec-council")

    def prompt(self, spec_text, seat="security"):
        self.write(os.path.join("spec-council", "U", "prompts", seat + ".md"), PROMPT % spec_text)

    def council(self, council, council_dir=None, unit=None):
        unit = unit or open_unit()
        found = plan_lint.lint_unit(unit, {"units": [unit]}, root=self.root, council=council,
                                    council_dir=self.council_dir if council_dir is None else council_dir)
        return [f for f in found if f["rule"] in ("COUNCIL", "COUNCIL-ABSENT")]

    def test_fix_first_is_blocking_with_its_claims(self):
        self.prompt(SPEC)
        got = self.council({"U": entry("FIX-FIRST", CLAIMS)})
        self.assertEqual(sev(got), [("COUNCIL", "BLOCKING")])
        self.assertIn("FIX-FIRST", got[0]["text"])
        for claim in CLAIMS:
            self.assertIn(claim, got[0]["text"])

    def test_do_not_build_is_blocking_with_its_claims(self):
        self.prompt(SPEC)
        got = self.council({"U": entry("DO-NOT-BUILD", CLAIMS)})
        self.assertEqual(sev(got), [("COUNCIL", "BLOCKING")])
        self.assertIn("DO-NOT-BUILD", got[0]["text"])
        self.assertIn(CLAIMS[1], got[0]["text"])

    def test_council_no_data_state_is_blocking(self):
        self.prompt(SPEC)
        got = self.council({"U": entry("NO-DATA")})
        self.assertEqual(sev(got), [("COUNCIL", "BLOCKING")])
        self.assertIn("NO-DATA", got[0]["text"])

    def test_unreadable_blockers_still_hold(self):
        self.prompt(SPEC)
        got = self.council({"U": {"state": "FIX-FIRST", "blockers": "the gate fails open"}})
        self.assertEqual(sev(got), [("COUNCIL", "BLOCKING")])

    def test_design_clear_on_the_current_spec_has_no_council_finding(self):
        self.prompt(SPEC)
        self.assertEqual(self.council({"U": entry("DESIGN-CLEAR")}), [])

    def test_council_verdict_on_an_older_spec_is_stale(self):
        self.prompt(OLD_SPEC)
        got = self.council({"U": entry("DESIGN-CLEAR")})
        self.assertEqual(sev(got), [("COUNCIL", "BLOCKING")])
        self.assertIn("STALE", got[0]["text"])
        self.prompt(SPEC, seat="assurance")
        self.assertEqual(self.council({"U": entry("DESIGN-CLEAR")}), [])

    def test_council_without_prompts_is_no_data(self):
        self.assertEqual(sev(self.council({"U": entry("DESIGN-CLEAR")})), [("COUNCIL", "NO-DATA")])
        os.makedirs(os.path.join(self.council_dir, "U", "prompts"))
        self.assertEqual(sev(self.council({"U": entry("DESIGN-CLEAR")})), [("COUNCIL", "NO-DATA")])

    def test_absent_entry_is_advisory(self):
        self.prompt(SPEC)
        got = self.council({"OTHER": entry("FIX-FIRST", CLAIMS)})
        self.assertEqual(sev(got), [("COUNCIL-ABSENT", "ADVISORY")])

    def test_corrupt_council_file_is_no_data(self):
        self.prompt(SPEC)
        cases = [
            ("truncated", self.write("c/truncated.json", '{"U": {"state": "FIX')),
            ("not utf-8", self.write("c/bytes.json", b'\xff\xfe{"U": 1}')),
            ("not an object", self.write("c/list.json", "[]")),
            ("a directory", os.path.join(self.root, "c")),
            ("missing", os.path.join(self.root, "c", "absent.json")),
        ]
        for name, path in cases:
            with self.subTest(name):
                entries, problem = plan_lint.load_council(path)
                self.assertIsNone(entries)
                self.assertTrue(problem)
                self.assertEqual(sev(self.council(path)), [("COUNCIL", "NO-DATA")])

    def test_readable_council_file_is_held(self):
        self.prompt(SPEC)
        path = self.write("c/council.json", json.dumps({"U": entry("FIX-FIRST", CLAIMS)}))
        entries, problem = plan_lint.load_council(path)
        self.assertEqual(problem, "")
        self.assertEqual(entries["U"]["state"], "FIX-FIRST")
        self.assertEqual(sev(self.council(path)), [("COUNCIL", "BLOCKING")])

    def test_hostile_council_inputs_are_refused(self):
        self.prompt(SPEC)
        hostile = [5, True, 1.5, float("nan"), b"{}", ["U"], ("U",), {"U": "FIX-FIRST"}, {"U": None},
                   {"U": {"state": None}}, {"U": {"state": ["FIX-FIRST"]}}, {"U": {"state": {"x": 1}}},
                   {"U": {"state": float("nan")}}, {"U": {"state": True}}, {"U": {"state": "MAYBE"}}, {"U": {}}]
        for bad in hostile:
            with self.subTest(council=repr(bad)):
                self.assertEqual(sev(self.council(bad)), [("COUNCIL", "NO-DATA")])
        for bad in (5, True, float("nan"), b"dir", ["d"]):
            with self.subTest(council_dir=repr(bad)):
                got = self.council({"U": entry("DESIGN-CLEAR")}, council_dir=bad)
                self.assertEqual(sev(got), [("COUNCIL", "NO-DATA")])
        for bad in (5, True, float("nan"), b"x", ["x"], "", {"a": 1}):
            with self.subTest(load=repr(bad)):
                entries, problem = plan_lint.load_council(bad)
                self.assertIsNone(entries)
                self.assertTrue(problem)

    def test_done_unit_has_no_council_finding(self):
        self.prompt(SPEC)
        self.assertEqual(self.council({"U": entry("FIX-FIRST", CLAIMS)}, unit=open_unit("DONE")), [])

    def test_no_council_argument_adds_no_council_finding(self):
        unit = open_unit()
        found = plan_lint.lint_unit(unit, {"units": [unit]}, root=self.root)
        self.assertEqual([f for f in found if f["rule"] in ("COUNCIL", "COUNCIL-ABSENT")], [])


class RolesTest(_Home):
    """ROLE-CONTENT: the roles file's content class against the class each call site passes."""

    def setUp(self):
        _Home.setUp(self)
        for rel, text in SITES.items():
            self.write(rel, text)

    def roles(self, **changes):
        """A roles file: a class per role changed by name; None drops the content key, "DROP" drops the role."""
        roles = {}
        for role, content in CLASSES.items():
            roles[role] = {"does": role, "when": "inside", "kind": "build", "content": content,
                           "must_be_chosen": False}
        for role, content in changes.items():
            if content == "DROP":
                roles.pop(role)
            elif content is None:
                roles[role].pop("content")
            else:
                roles[role]["content"] = content
        return self.write("roles.json", json.dumps({"schema": "test", "roles": roles}))

    def lint(self, path):
        return plan_lint.lint_roles(path, root=self.root)

    def test_finisher_private_against_public_call_site_is_flagged(self):
        got = self.lint(self.roles())
        self.assertEqual([(f["unit"], f["rule"], f["severity"]) for f in got], [("-", "ROLE-CONTENT", "BLOCKING")])
        for word in ("finisher", "private", "public"):
            self.assertIn(word, got[0]["text"])

    def test_agreeing_roles_have_no_finding(self):
        self.assertEqual(self.lint(self.roles(finisher="public")), [])

    def test_call_sites_resolve_constants_and_router_classes(self):
        self.assertEqual(plan_lint.call_site_class(self.root, "scripts/loop/probe_wave.py", "sensitivity"), ("public", ""))
        self.assertEqual(plan_lint.call_site_class(self.root, "scripts/loop/unit_runner.py", "sensitivity"), ("public", ""))
        self.assertEqual(plan_lint.call_site_class(self.root, "scripts/loop/check_wave.py", "call_one"), ("public", ""))

    def test_unknown_content_class_is_a_finding(self):
        got = self.lint(self.roles(finisher="public", worker="secret"))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "BLOCKING")])
        for word in ("worker", "secret", "is not one of"):
            self.assertIn(word, got[0]["text"])

    def test_outside_role_with_unknown_class_is_a_finding(self):
        got = self.lint(self.roles(finisher="public", documenter="secret"))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "BLOCKING")])
        self.assertIn("documenter", got[0]["text"])

    def test_role_without_content_is_a_finding(self):
        got = self.lint(self.roles(finisher="public", checker=None))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "BLOCKING")])
        self.assertIn("checker", got[0]["text"])
        self.assertIn("no content class", got[0]["text"])

    def test_role_with_a_call_site_missing_from_the_roles_file_is_a_finding(self):
        got = self.lint(self.roles(finisher="public", checker="DROP"))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "BLOCKING")])
        self.assertIn("checker", got[0]["text"])

    def test_unresolvable_call_site_value_is_no_data(self):
        sites = {
            "a variable": 'import model_call\ncls = "public"\nmodel_call.call_one("m", "p", "grade", cls)\n',
            "too few arguments": 'import model_call\nmodel_call.call_one("m", "p", "grade")\n',
        }
        for name, text in sites.items():
            with self.subTest(name):
                self.write("scripts/loop/check_wave.py", text)
                got = self.lint(self.roles(finisher="public"))
                self.assertEqual(sev(got), [("ROLE-CONTENT", "NO-DATA")])
                self.assertIn("checker", got[0]["text"])

    def test_disagreeing_call_sites_are_no_data(self):
        self.write("scripts/loop/check_wave.py", 'import model_call\nimport model_router as R\n'
                   'model_call.call_one("m", "p", "grade", "public")\nmodel_call.call_one("m", "p", "grade", R.PRIVATE)\n')
        got = self.lint(self.roles(finisher="public"))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "NO-DATA")])
        self.assertIn("disagree", got[0]["text"])

    def test_missing_empty_or_broken_call_site_is_no_data(self):
        cases = [("no call", "x = 1\n"), ("unparseable", "def (\n"), ("not utf-8", b"\xff\xfe x = 1\n")]
        for name, text in cases:
            with self.subTest(name):
                self.write("scripts/loop/finish_run.py", text)
                got = self.lint(self.roles(finisher="public"))
                self.assertEqual(sev(got), [("ROLE-CONTENT", "NO-DATA")])
                self.assertIn("finisher", got[0]["text"])
        os.remove(os.path.join(self.root, "scripts/loop/finish_run.py"))
        got = self.lint(self.roles(finisher="public"))
        self.assertEqual(sev(got), [("ROLE-CONTENT", "NO-DATA")])

    def test_unreadable_roles_file_is_no_data(self):
        cases = [
            ("missing", os.path.join(self.root, "absent.json")),
            ("a directory", self.root),
            ("truncated", self.write("r/truncated.json", '{"roles": ')),
            ("not utf-8", self.write("r/bytes.json", b'\xff{"roles": {}}')),
            ("roles a list", self.write("r/list.json", '{"roles": []}')),
            ("roles empty", self.write("r/empty.json", '{"roles": {}}')),
            ("not an object", self.write("r/top.json", "[]")),
        ]
        for name, path in cases:
            with self.subTest(name):
                got = self.lint(path)
                self.assertEqual([(f["unit"], f["rule"], f["severity"]) for f in got],
                                 [("-", "ROLE-CONTENT", "NO-DATA")])

    def test_hostile_arguments_are_refused(self):
        good = self.roles(finisher="public")
        for bad in (5, True, 1.5, float("nan"), b"roles.json", ["roles.json"], ""):
            with self.subTest(roles_path=repr(bad)):
                self.assertEqual(sev(plan_lint.lint_roles(bad, root=self.root)), [("ROLE-CONTENT", "NO-DATA")])
        for bad in (None, 5, True, float("nan"), b".", ["."], ""):
            with self.subTest(root=repr(bad)):
                self.assertEqual(sev(plan_lint.lint_roles(good, root=bad)), [("ROLE-CONTENT", "NO-DATA")])
        for args in ((None, "scripts/loop/finish_run.py", "call_one"), (self.root, 5, "call_one"),
                     (self.root, "scripts/loop/finish_run.py", None), (self.root, "scripts/loop/finish_run.py", ["call_one"]),
                     (self.root, "scripts/loop/finish_run.py", "other"), (b".", b"x.py", "call_one")):
            with self.subTest(call_site=repr(args)):
                value, problem = plan_lint.call_site_class(*args)
                self.assertIsNone(value)
                self.assertTrue(problem)
        odd = self.write("r/odd.json", json.dumps({"roles": {"worker": "public", "adversary": ["public"]}}))
        got = plan_lint.lint_roles(odd, root=self.root)
        self.assertTrue(got)
        self.assertEqual(set(f["severity"] for f in got), {"BLOCKING"})

    def test_default_path_reads_the_roles_environment(self):
        os.environ["BROTHER_LOOP_ROLES"] = self.roles()
        got = plan_lint.lint_roles(root=self.root)
        self.assertEqual(sev(got), [("ROLE-CONTENT", "BLOCKING")])
        self.assertIn("finisher", got[0]["text"])

    def test_call_site_table_names_the_four_inside_roles(self):
        self.assertEqual([row[0] for row in plan_lint.ROLE_SITES], ["worker", "adversary", "checker", "finisher"])
        for role, path, how in plan_lint.ROLE_SITES:
            self.assertTrue(path.startswith("scripts/loop/") and path.endswith(".py"), path)
            self.assertIn(how, ("call_one", "sensitivity"))

    def test_each_call_site_exists_in_the_tree(self):
        paths = [os.path.join(REPO, path) for _, path, _ in plan_lint.ROLE_SITES]
        if not all(os.path.isfile(p) for p in paths):
            self.skipTest("the call site files are not in this tree")
        for (role, path, how), real in zip(plan_lint.ROLE_SITES, paths):
            with self.subTest(role):
                with open(real, "rb") as fh:
                    text = fh.read().decode("utf-8", "replace")
                self.assertIn(how, text)


if __name__ == "__main__":
    unittest.main()
