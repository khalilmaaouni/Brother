"""AS1.a: delivery shows all five assurance gates, never only numbers.

receipt_door.assurance_matrix runs every BrotherSBE hard gate
(sbe_gate.GATES: numbers, migration, approval, ran, proof) over a project
root the way sbe_gate.main does, marks which of them the receipt's risk
makes required (receipt_door.required_gates), and says assurance is
complete only when every required gate is PASS and no gate at all FAILs.
`receipt_door.py --verdict R --assurance` prints it after the unit lines.

Spec: docs/plan/specs/AS1.md, section AS1.a (REQ-AS1-01 to REQ-AS1-08).
Done check: python3 -B scripts/test_assurance_matrix.py
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import codex_surface  # noqa: E402
import receipt_door as rd  # noqa: E402

sbe_gate, sbe_checks, _why = rd._sbe_gate_modules()
DOOR = os.path.join(HERE, "receipt_door.py")
PLAIN = {"units": [{"unit": "A1", "evidence_family": "E1"}]}


def write_files(root, files):
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(content, str):
                fh.write(content)
            else:
                json.dump(content, fh)


def fixture(name):
    return sbe_gate.GATES[name].full_fixture["files"]


class _Project(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brother-assurance-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def matrix(self, view=PLAIN, risk=()):
        return rd.assurance_matrix(self.root, view, list(risk))

    def verdicts(self, m):
        return {n: g["verdict"] for n, g in m["gates"].items()}


class GateSet(_Project):
    """REQ-AS1-01; mutation M-AS1-ONLY-NUMBERS."""

    def test_modules_import(self):
        self.assertIsNotNone(sbe_gate, _why)

    def test_names_equal_the_registry(self):
        m = self.matrix()
        self.assertEqual(rd.GATE_NAMES, tuple(sbe_gate.GATES))
        self.assertEqual(tuple(m["gates"]), tuple(sbe_gate.GATES))

    def test_sixth_gate_cannot_hide(self):
        extra = dict(sbe_gate.GATES, sixth=sbe_gate.GATES["ran"])
        with mock.patch.object(sbe_gate, "GATES", extra):
            m = self.matrix()
        self.assertEqual(m["gates"]["gate-set"]["verdict"], "FAIL")
        self.assertIn("sixth", m["gates"]["gate-set"]["evidence"])
        self.assertFalse(m["assurance_complete"])

    def test_unimportable_gate_module_is_no_data(self):
        with mock.patch.object(rd, "_sbe_gate_modules",
                               return_value=(None, None, "NO-DATA: gone")):
            m = self.matrix()
        for name in rd.GATE_NAMES:
            self.assertEqual(m["gates"][name]["verdict"], "NO-DATA")
        self.assertIn("gone", m["gates"]["gate-set"]["evidence"])
        self.assertFalse(m["assurance_complete"])


class Unrun(_Project):
    """REQ-AS1-02; mutation M-AS1-UNRUN-IS-PASS."""

    def test_empty_project_is_five_no_data(self):
        m = self.matrix()
        self.assertEqual(self.verdicts(m), dict.fromkeys(rd.GATE_NAMES,
                                                         "NO-DATA"))
        for g in m["gates"].values():
            self.assertEqual(g["evidence"], "")
        self.assertFalse(m["assurance_complete"])

    def test_unreadable_root_is_no_data(self):
        m = rd.assurance_matrix(os.path.join(self.root, "absent"), PLAIN, [])
        self.assertEqual(set(self.verdicts(m).values()), {"NO-DATA"})
        self.assertFalse(m["assurance_complete"])
        self.assertIn("not a readable directory", m["note"])


class PassFixtures(_Project):
    """REQ-AS1-03: the gates' own worked examples read PASS with the path."""

    def check(self, name):
        write_files(self.root, fixture(name))
        g = self.matrix()["gates"][name]
        self.assertEqual(g["verdict"], "PASS", g)
        self.assertEqual(g["evidence"], sbe_gate.GATES[name].reads[0])

    def test_numbers(self):
        self.check("numbers")

    def test_migration(self):
        self.check("migration")

    def test_ran(self):
        self.check("ran")

    def test_proof(self):
        self.check("proof")

    def test_modes(self):
        m = self.matrix()
        self.assertEqual(m["gates"]["numbers"]["mode"], "automated")
        for name in ("migration", "approval", "ran", "proof"):
            self.assertEqual(m["gates"][name]["mode"], "manual")


class Completeness(_Project):
    """REQ-AS1-04; mutation M-AS1-COMPLETE-LAX."""

    def test_table(self):
        broken_numbers = {"numbers-manifest.json": {"figures": [{
            "label": "gmv", "snapshot_id": "s", "query": "q",
            "second_derivation": "q2",
            "rerun": {"ran": True, "primary": 1, "secondary": 2}}]}}
        cases = (
            ("all required PASS", [fixture("proof")], True),
            ("one required NO-DATA", [fixture("ran")], False),
            ("optional FAIL", [fixture("proof"), broken_numbers], False),
        )
        for label, file_sets, expect in cases:
            with self.subTest(label):
                root = tempfile.mkdtemp(prefix="brother-assurance-")
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                for files in file_sets:
                    write_files(root, files)
                m = rd.assurance_matrix(root, PLAIN, [])
                self.assertIs(m["assurance_complete"], expect, m)
        self.assertEqual(m["gates"]["numbers"]["verdict"], "FAIL")
        self.assertFalse(m["gates"]["numbers"]["required"])


class Required(unittest.TestCase):
    """REQ-AS1-05: the required set follows the receipt."""

    def test_risk_cases(self):
        e8 = {"units": [{"unit": "A1", "evidence_family": "E8"}]}
        cases = (
            (PLAIN, [], ["ran", "proof"]),
            (PLAIN, ["migration"], ["migration", "approval", "ran", "proof"]),
            (PLAIN, ["money"], ["approval", "ran", "proof"]),
            (e8, ["encoding"], ["numbers", "ran", "proof"]),
        )
        for view, risk, expect in cases:
            with self.subTest(risk=risk):
                self.assertEqual(rd.required_gates(view, risk), expect)


class Waivers(_Project):
    """REQ-AS1-06; mutation M-AS1-WAIVED-PASS."""

    def test_waived_gate_is_no_data(self):
        write_files(self.root, {"old/" + k: v
                                for k, v in fixture("ran").items()})
        write_files(self.root, {"old/.sbe-exempt":
                                "gates: ran\nreason: kept for history\n"})
        g = self.matrix()["gates"]["ran"]
        self.assertEqual(g["verdict"], "NO-DATA", g)

    def test_broken_exemption_is_fail(self):
        write_files(self.root, fixture("ran"))
        write_files(self.root, {".sbe-exempt": "gates: *\nreason: all\n"})
        m = self.matrix()
        self.assertEqual(m["gates"]["ran"]["verdict"], "FAIL")
        self.assertFalse(m["assurance_complete"])


class Disclosure(_Project):
    """REQ-AS1-07; mutation M-AS1-NO-DISCLOSE."""

    def test_every_rendering_discloses(self):
        for files in ({}, fixture("proof"), fixture("numbers")):
            write_files(self.root, files)
            lines = rd.assurance_lines(self.matrix())
            self.assertIn(rd.ASSURANCE_DISCLOSURE, lines)
            self.assertTrue(rd.ASSURANCE_DISCLOSURE.startswith(
                "4 of 5 assurance gates are not run by the engine"))
            self.assertEqual(sum(1 for l in lines if l.startswith("GATE ")),
                             5)
            self.assertTrue(any(l.startswith("ASSURANCE COMPLETE: ")
                                for l in lines))

    def test_line_shape(self):
        write_files(self.root, fixture("proof"))
        lines = rd.assurance_lines(self.matrix())
        self.assertIn("GATE ran | PASS | required | evidence ran-receipt.json"
                      " | manual", lines)
        self.assertIn("GATE numbers | NO-DATA | not required | evidence none"
                      " | automated", lines)
        self.assertIn("ASSURANCE COMPLETE: yes", lines)

    def test_incomplete_names_the_gates(self):
        lines = rd.assurance_lines(self.matrix())
        self.assertIn("ASSURANCE COMPLETE: no, because ran, proof", lines)


class Crash(_Project):
    """REQ-AS1-08; mutation M-AS1-HIDE-CRASH."""

    def test_raising_gate_is_fail(self):
        def boom(root):
            raise RuntimeError("gate exploded")
        broken = copy.copy(sbe_gate.GATES["ran"])
        broken.fn = boom
        write_files(self.root, fixture("proof"))
        with mock.patch.dict(sbe_gate.GATES, {"ran": broken}):
            m = self.matrix()
        self.assertEqual(m["gates"]["ran"]["verdict"], "FAIL")
        self.assertFalse(m["assurance_complete"])


class TheFlag(unittest.TestCase):
    """`--verdict R --assurance`: the matrix after the unit lines, exit 1
    on a FAIL gate; the deliver skill names the flag."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-assurance-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def receipt(self, project):
        run_dir = os.path.join(self.tmp, "run")
        path = os.path.join(run_dir, "receipt", "receipt.json")
        body = {k: "x" for k in _br.RECEIPT_REQUIRED_FIELDS}
        e = {"id": "A1", "state": "verified", "command": "make check",
             "exit_code": 0, "evidence_family": "E1"}
        body.update(scope={"changed": [{"unit": "A1", "file": "f.txt"}]},
                    evidence=[e], unproven=[],
                    attention={"risk_triggers": []},
                    host_capability={"receipt_path": path})
        write_files(run_dir, {"receipt/receipt.json": body,
                              "target.json": {"cwd": project}})
        return path

    def run_door(self, *args):
        proc = subprocess.run([sys.executable, "-B", DOOR] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=120)
        return proc.returncode, proc.stdout

    def test_complete_project(self):
        project = os.path.join(self.tmp, "proj")
        write_files(project, fixture("proof"))
        code, out = self.run_door("--verdict", self.receipt(project),
                                  "--assurance")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        unit = next(i for i, l in enumerate(lines) if l.startswith("UNIT "))
        first_gate = next(i for i, l in enumerate(lines)
                          if l.startswith("GATE "))
        self.assertLess(unit, first_gate)
        self.assertIn("ASSURANCE COMPLETE: yes", lines)
        self.assertIn(rd.ASSURANCE_DISCLOSURE, lines)

    def test_failing_gate_exits_one(self):
        project = os.path.join(self.tmp, "proj")
        write_files(project, fixture("proof"))
        write_files(project, {".sbe-exempt": "gates: *\nreason: all\n"})
        code, out = self.run_door("--verdict", self.receipt(project),
                                  "--assurance")
        self.assertEqual(code, 1, out)

    def test_without_flag_no_gate_lines(self):
        project = os.path.join(self.tmp, "proj")
        code, out = self.run_door("--verdict", self.receipt(project))
        self.assertNotIn("GATE ", out)

    def test_deliver_skill_names_the_flag(self):
        body = dict((v, b) for v, _d, b in codex_surface.VERBS)["deliver"]
        self.assertIn("--assurance", body)
        self.assertIn("all five gate lines", body)


if __name__ == "__main__":
    unittest.main()
