"""TV1.b: the one command the status and deliver skills call, and the
agreement between its readers.

`python3 receipt_door.py --verdict RECEIPT [--stdout-file F --exit-code N]`
prints receipt_door.verdict_lines of receipt_door.read_receipt_verdict,
prints `DISAGREES: <why>` when the run's own output disagrees with its
receipt, and exits with the view's exit code. Anything else on the command
line is an argparse refusal, exit 2, nothing read. The generated status and
deliver skills name the command instead of asking the reader to judge a
receipt by eye.

Spec: docs/plan/specs/TV1.md, section TV1.b (REQ-TV1-07 to REQ-TV1-11).
Done check: python3 -B scripts/test_verdict_surfaces.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import codex_surface  # noqa: E402

DOOR = os.path.join(HERE, "receipt_door.py")
COMMAND = 'receipt_door.py" --verdict'
OLD = "open that receipt and report every entry"
V, N, R = "verified", "no-data", "refused"


def entry(uid, state):
    e = {"id": uid, "state": state, "command": "make check", "exit_code": 0}
    if state != V:
        e["reason"] = "%s was not proven" % uid
    return e


def run(*args):
    proc = subprocess.run([sys.executable, "-B", DOOR] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


class _Fixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-verdict-surfaces-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, name="receipt"):
        return os.path.join(self.tmp, "%s-%d.json"
                            % (name, len(os.listdir(self.tmp))))

    def receipt(self, entries):
        path = self.path()
        body = {k: "x" for k in _br.RECEIPT_REQUIRED_FIELDS}
        body.update(scope={"changed": [{"unit": e["id"], "file": "f.txt"}
                                       for e in entries]},
                    evidence=[e for e in entries if e["state"] == V],
                    unproven=[e for e in entries if e["state"] != V],
                    attention={"risk_triggers": []},
                    host_capability={"receipt_path": path})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        return path


class TheCommand(_Fixture):
    """REQ-TV1-07, REQ-TV1-08, REQ-TV1-09; mutations M-TV1-EXIT-ZERO and
    M-TV1-ACCEPT-EXTRA."""

    CASES = {
        "DONE": [entry("A1", V)],
        "PARTIAL": [entry("A1", V), entry("N1", N)],
        "UNVERIFIED": [entry("N1", N)],
        "NOT DONE": [entry("A1", V), entry("F1", R)],
    }

    def test_exit_code_equals_the_engines_for_every_state(self):
        seen = set()
        for state, entries in self.CASES.items():
            with self.subTest(state=state):
                refused = [(e["id"], e["reason"]) for e in entries
                           if e["state"] == R]
                code, _ = _br._exit_code_for(entries, refused)
                rc, out, err = run("--verdict", self.receipt(entries))
                self.assertEqual(rc, code, err)
                self.assertTrue(out.startswith(_br.RESULT_PREFIX + state
                                               + " |"), out)
                seen.add(rc)
        self.assertEqual(seen, {0, 1, 2})

    def test_a_missing_receipt_exits_1_and_prints_not_done(self):
        rc, out, _ = run("--verdict", os.path.join(self.tmp, "absent.json"))
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith(_br.RESULT_PREFIX + "NOT DONE"))
        self.assertIn("RECEIPT: ", out)

    def test_a_directory_and_a_nul_byte_receipt_exit_1(self):
        nul = self.path("nul")
        with open(nul, "wb") as fh:
            fh.write(b'{"evidence": [\x00]}')
        for target in (self.tmp, nul):
            with self.subTest(target=target):
                rc, out, _ = run("--verdict", target)
                self.assertEqual(rc, 1)
                self.assertIn("NOT DONE", out)

    def test_fifty_units_print_fifty_unit_lines(self):
        rc, out, _ = run("--verdict", self.receipt(
            [entry("U%02d" % i, V) for i in range(50)]))
        self.assertEqual(rc, 0)
        self.assertEqual(len([ln for ln in out.splitlines()
                              if ln.startswith("UNIT ")]), 50)

    def test_anything_else_on_the_line_exits_2_and_reads_nothing(self):
        good = self.receipt([entry("A1", V)])
        for args in ([], ["--yes"], ["--verdict", good, "--yes"],
                     ["--verdict", good, "extra"],
                     ["--verdict", good, "--stdout-file", good],
                     ["--verdict", good, "--exit-code", "0"],
                     ["--verdict", good, "--exit-code", "zero",
                      "--stdout-file", good]):
            with self.subTest(args=args):
                rc, out, _ = run(*args)
                self.assertEqual(rc, 2)
                self.assertEqual(out, "")


class ThreeReadersAgree(_Fixture):
    """REQ-TV1-11, mutation M-TV1-STDOUT-TRUST: the command, the engine's
    read_run_result over the run's stdout, and the exit code."""

    def stdout_file(self, receipt, line):
        path = os.path.join(self.tmp, "stdout-%d.txt"
                            % len(os.listdir(self.tmp)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("unit output\n%s\nbrother_run: receipt: %s\n"
                     % (line, receipt))
        return path

    def test_pass_plus_nodata_is_2_in_all_three(self):
        entries = [entry("A1", V), entry("N1", N)]
        receipt = self.receipt(entries)
        line = _br.format_result_line(_br.delivery_conclusion(
            entries, [], [], True, [], None))
        out_file = self.stdout_file(receipt, line)
        with open(out_file, encoding="utf-8") as fh:
            state, why = _br.read_run_result(fh.read(), 2)
        self.assertEqual((state, why), ("PARTIAL", ""))
        rc, out, _ = run("--verdict", receipt, "--stdout-file", out_file,
                         "--exit-code", "2")
        self.assertEqual(_br.EXIT_FOR_STATE[state], 2)
        self.assertEqual(rc, 2)
        self.assertNotIn("DISAGREES", out)

    def test_a_forged_done_line_is_surfaced(self):
        receipt = self.receipt([entry("A1", V), entry("N1", N)])
        forged = _br.format_result_line(_br.delivery_conclusion(
            [entry("A1", V), entry("A2", V)], [], [], True, [], None))
        rc, out, _ = run("--verdict", receipt, "--stdout-file",
                         self.stdout_file(receipt, forged),
                         "--exit-code", "2")
        self.assertEqual(rc, 2)
        self.assertIn("DISAGREES: the line reads DONE but the exit code is 2",
                      out)

    def test_an_unreadable_stdout_file_is_surfaced(self):
        receipt = self.receipt([entry("A1", V)])
        rc, out, _ = run("--verdict", receipt, "--stdout-file",
                         os.path.join(self.tmp, "absent.txt"),
                         "--exit-code", "0")
        self.assertEqual(rc, 1)
        self.assertIn("DISAGREES: ", out)


class TheSkillsNameTheCommand(unittest.TestCase):
    """REQ-TV1-10, mutation M-TV1-SKILL-PROSE: generated in memory from
    render_skill, and as shipped in bundle/skills and bundle/codex-skills."""

    def test_status_and_deliver_name_the_command(self):
        expected = codex_surface.expected(Path(ROOT))
        for verb in ("status", "deliver"):
            rel = "skills/brother-%s/SKILL.md" % verb
            with self.subTest(verb=verb):
                text = expected[rel]
                self.assertIn(COMMAND, text)
                self.assertIn("treat its exit code as the run's exit code",
                              text)
                self.assertNotIn(OLD, text)
                for tree in ("skills", "codex-skills"):
                    shipped = os.path.join(ROOT, "bundle", tree,
                                           "brother-%s" % verb, "SKILL.md")
                    with open(shipped, encoding="utf-8") as fh:
                        body = fh.read()
                    self.assertIn(COMMAND, body)
                    self.assertNotIn(OLD, body)
        self.assertIn("No receipt, or a refused entry, is a NOT DONE report",
                      expected["skills/brother-deliver/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
