#!/usr/bin/env python3
"""The loop's receipt against the repository's receipt contract (AGENTS.md "The receipt contract"): every changed file
named with the exact check command that ran it, its exit code and where its output lives, in the engine's own per-file
shape, so receipt_door.require_per_file_checks reads a loop receipt the way it reads any other.

ENTRY POINT, NOT A HELPER: every case runs the receipt writer's real command line (scripts/loop/loop_receipt.py write ...)
as a subprocess against a run directory holding a real proof/landings.jsonl, then reads the receipt/receipt.json it wrote.
The landing rows are the ones scripts/loop/land_batch.record_landing writes (loop-landing-v1, plus files, checks,
done_check and log), built by hand here so each fixture isolates one condition:
  A  a landing with files and passing checks          -> per-file entries the engine's gate accepts, state no-data (never seen to fail)
  A2 the same sub landing a second time               -> a second, separate set of entries (keyed by commit)
  B  a landing whose row records no checks            -> state no-data, no exit code, never verified, gate refuses it
  E  a landing whose row records no files             -> ONE no-data entry naming the sub, gate refuses it
  C  a line that is not JSON                          -> a NO-DATA string entry, and the writer still exits 0
  One condition per fixture (mutation survivors of 2026-09-30, each of these dies to exactly one guard):
  F  checks is an EMPTY list (not a missing key)      -> no-data, no exit code, the reason never says "exited 0"
  G  files is an EMPTY list (not a missing key)       -> ONE no-data entry, the landing never vanishes from scope
  H  a check whose exit_code is a bool                -> refused as not an integer
  I  a check whose command is blank, among real ones  -> refused
  J  a file entry that is not text, among real ones   -> ONE no-data entry
  K  evidence[].done_check                            -> the build's declared command, or NO-DATA when not recorded

Lives in scripts/ (not scripts/loop/) because it imports scripts/receipt_door.py.

Run: python3 scripts/test_loop_receipt_contract.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import receipt_door  # noqa: E402

WRITER = os.path.join(HERE, "loop", "loop_receipt.py")
COMMIT_A, COMMIT_A2, COMMIT_B, COMMIT_E = "a1" * 20, "a2" * 20, "b1" * 20, "e1" * 20
LOG = "/evidence/land-batch/2026-09-30-060000.log"
CHECKS = [
    {"label": "land_apply A.1", "command": "/usr/bin/python3 /repo/scripts/loop/land_apply.py /runs/A.1-r1-build.json", "exit_code": 0},
    {"label": "registration", "command": "/usr/bin/python3 -B scripts/test_battery_registration.py", "exit_code": 0},
    {"label": "commit_scan", "command": "/usr/bin/python3 /repo/scripts/loop/commit_scan.py", "exit_code": 0},
]


def landing(commit, subs, **extra):
    row = {"schema": "loop-landing-v1", "commit": commit, "subs": subs, "builds": ["/runs/x-r1-build.json"],
           "verdict": "LANDED", "remote_ref": "hub/main", "remote_sha": commit, "fetched_at": "2026-09-30T00:00:01+00:00",
           "at": "2026-09-30T00:00:00+00:00"}
    row.update(extra)
    return json.dumps(row, sort_keys=True)


class LoopReceiptContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-receipt-contract-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = os.path.join(self.tmp, "run-x")
        os.makedirs(os.path.join(self.run_dir, "proof"))
        os.makedirs(os.path.join(self.tmp, "home"))
        self.lines = [
            landing(COMMIT_A, ["A.1"], files=["scripts/a.py", "scripts/test_a.py"], checks=CHECKS,
                    done_check={"A.1": "python3 -B scripts/test_a.py"}, log=LOG),
            landing(COMMIT_B, ["B.1"], files=["scripts/b.py"]),
            "{this line is not json",
            landing(COMMIT_A2, ["A.1"], files=["scripts/a.py"], checks=CHECKS, log=LOG),
            landing(COMMIT_E, ["E.1"], checks=CHECKS, log=LOG),
        ]

    def write(self, lines=None, record=True):
        if record:
            with open(os.path.join(self.run_dir, "proof", "landings.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.lines if lines is None else lines) + "\n")
        env = dict(os.environ, HOME=os.path.join(self.tmp, "home"), TMPDIR=self.tmp, BROTHER_DISK_GATE="off")
        proc = subprocess.run([sys.executable, "-B", WRITER, "write", "--run-dir", self.run_dir, "--pid", "1",
                               "--start", "2026-01-01T00:00:00", "--end", "2030-01-01T00:00:00", "--state", "FINISHED",
                               "--reason", "fixture", "--deadline", "23:59", "--budget", "10", "--log-path", "/no/such/log",
                               "--cwd", self.tmp], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, "the writer must exit 0 with a receipt written: %s%s" % (proc.stdout, proc.stderr))
        path = os.path.join(self.run_dir, "receipt", "receipt.json")
        self.assertEqual(proc.stdout.strip().splitlines()[-1], path)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def entries(receipt, unit):
        return [e for e in receipt["scope"]["changed"] if isinstance(e, dict) and e["unit"] == unit]

    def test_a_landing_with_files_and_passing_checks_passes_the_engines_gate(self):
        entries = self.entries(self.write(), "A.1@" + COMMIT_A[:12])
        self.assertEqual([e["file"] for e in entries], ["scripts/a.py", "scripts/test_a.py"])
        self.assertEqual(receipt_door.require_per_file_checks(entries), (True, ""))
        for e in entries:
            self.assertEqual(e["check_command"], " && ".join(c["command"] for c in CHECKS))
            self.assertEqual(e["exit_code"], 0)
            self.assertEqual(e["output_location"], LOG)
            self.assertEqual(e["check_passed_before"], "NO-DATA")   # named, not guessed
            # receipt_door's rule: a check never seen to fail is not verified, whatever its exit code.
            self.assertEqual(e["state"], "no-data")
            self.assertIn("check_passed_before is NO-DATA", e["reason"])
            self.assertEqual(set(e), {"file", "unit", "check_command", "exit_code", "output_location",
                                      "check_passed_before", "state", "reason"})

    def test_the_same_sub_landing_twice_is_two_entries(self):
        receipt = self.write()
        first, second = self.entries(receipt, "A.1@" + COMMIT_A[:12]), self.entries(receipt, "A.1@" + COMMIT_A2[:12])
        self.assertEqual((len(first), len(second)), (2, 1))

    def test_a_landing_whose_row_records_no_checks_is_no_data_never_verified(self):
        receipt = self.write()
        entries = self.entries(receipt, "B.1@" + COMMIT_B[:12])
        self.assertEqual([e["file"] for e in entries], ["scripts/b.py"])
        self.assertEqual(entries[0]["state"], "no-data")
        self.assertIsNone(entries[0]["exit_code"])
        self.assertEqual(entries[0]["check_command"], "")
        self.assertTrue(entries[0]["reason"].startswith("NO-DATA"), entries[0]["reason"])
        self.assertFalse(receipt_door.require_per_file_checks(entries)[0])

    def test_a_landing_whose_row_records_no_files_is_one_no_data_entry_naming_the_sub(self):
        entries = self.entries(self.write(), "E.1@" + COMMIT_E[:12])
        self.assertEqual(len(entries), 1)
        self.assertIn("E.1@", entries[0]["file"])
        self.assertEqual(entries[0]["state"], "no-data")
        self.assertIsNone(entries[0]["exit_code"])
        self.assertFalse(receipt_door.require_per_file_checks(entries)[0])

    def test_a_corrupt_line_is_a_no_data_entry_and_the_receipt_is_still_written(self):
        receipt = self.write()
        strings = [e for e in receipt["scope"]["changed"] if isinstance(e, str)]
        self.assertEqual(len(strings), 1, receipt["scope"]["changed"])
        self.assertTrue(strings[0].startswith("NO-DATA"), strings[0])
        self.assertIn("row 3", strings[0])   # the third line of the fixture, named
        self.assertEqual([e for e in receipt["evidence"] if isinstance(e, str)][0][:7], "NO-DATA")
        self.assertEqual(len(receipt["evidence"]), len(self.lines))   # one evidence item per landing row, readable or not
        # a run with a corrupt or hollow landing is never read as fully proven
        self.assertFalse(receipt_door.require_per_file_checks(receipt["scope"]["changed"])[0])

    def test_a_row_that_is_valid_json_but_not_a_landing_is_no_data_too(self):
        receipt = self.write(lines=["[1, 2, 3]", '{"schema": "something-else", "commit": "abc", "subs": []}', "17"])
        self.assertEqual(len(receipt["scope"]["changed"]), 3)
        self.assertTrue(all(isinstance(e, str) and e.startswith("NO-DATA") for e in receipt["scope"]["changed"]))

    def test_a_nonzero_check_keeps_its_exit_code_and_is_not_verified(self):
        bad = [dict(CHECKS[0], exit_code=3)] + CHECKS[1:]
        entries = self.entries(self.write(lines=[landing(COMMIT_A, ["A.1"], files=["scripts/a.py"], checks=bad, log=LOG)]),
                               "A.1@" + COMMIT_A[:12])
        self.assertEqual((entries[0]["exit_code"], entries[0]["state"]), (3, "no-data"))

    def test_a_landing_record_that_cannot_be_read_is_one_no_data_note_and_the_receipt_is_still_written(self):
        os.mkdir(os.path.join(self.run_dir, "proof", "landings.jsonl"))   # exists, opens as a directory: an OSError, not a missing file
        receipt = self.write(record=False)
        for field in (receipt["scope"]["changed"], receipt["evidence"]):
            self.assertEqual(len(field), 1, field)
            self.assertTrue(field[0].startswith("NO-DATA: the landing record could not be read"), field)
        self.assertFalse(receipt_door.require_per_file_checks(receipt["scope"]["changed"])[0])

    def one(self, **row):
        """The entries of one landing written alone, so the fixture isolates exactly the condition in `row`."""
        return self.entries(self.write(lines=[landing(COMMIT_A, ["A.1"], **row)]), "A.1@" + COMMIT_A[:12])

    def refused(self, entries, why):
        self.assertEqual((entries[0]["state"], entries[0]["exit_code"], entries[0]["check_command"]), ("no-data", None, ""))
        self.assertIn(why, entries[0]["reason"])
        self.assertFalse(receipt_door.require_per_file_checks(entries)[0])

    def test_f_an_empty_checks_list_is_no_data_and_never_reads_as_every_check_exited_0(self):
        entries = self.one(files=["scripts/a.py"], checks=[], log=LOG)
        self.assertEqual(len(entries), 1)
        self.refused(entries, "records no checks")
        self.assertNotIn("exited 0", entries[0]["reason"])

    def test_g_an_empty_files_list_is_one_no_data_entry_not_a_vanished_landing(self):
        entries = self.one(files=[], checks=CHECKS, log=LOG)
        self.assertEqual(len(entries), 1, "the landing stays in scope as one entry")
        self.assertIn("A.1@", entries[0]["file"])
        self.refused(entries, "records no files")

    def test_h_a_bool_exit_code_is_refused_as_not_an_integer(self):
        entries = self.one(files=["scripts/a.py"], checks=[dict(CHECKS[0], exit_code=True)], log=LOG)
        self.refused(entries, "no integer exit code")

    def test_i_a_blank_command_among_real_ones_is_refused(self):
        entries = self.one(files=["scripts/a.py"], checks=[CHECKS[0], dict(CHECKS[1], command="   "), CHECKS[2]], log=LOG)
        self.refused(entries, "no command")

    def test_j_a_file_entry_that_is_not_text_is_one_no_data_entry(self):
        entries = self.one(files=["scripts/a.py", 5], checks=CHECKS, log=LOG)
        self.assertEqual(len(entries), 1)
        self.refused(entries, "records no files")

    def test_k_evidence_carries_the_declared_done_check_or_says_it_was_not_recorded(self):
        receipt = self.write()
        by_unit = {e["unit"]: e for e in receipt["evidence"] if isinstance(e, dict)}
        self.assertEqual(by_unit["A.1@" + COMMIT_A[:12]]["done_check"], {"A.1": "python3 -B scripts/test_a.py"})
        self.assertEqual(by_unit["A.1@" + COMMIT_A2[:12]]["done_check"], "NO-DATA: the build done_check was not recorded")
        self.assertEqual(by_unit["A.1@" + COMMIT_A[:12]]["checks"], CHECKS)

    def test_l_an_unverified_row_carries_its_parity_note_into_the_evidence(self):
        receipt = self.write(lines=[landing(COMMIT_A, ["A.1"], files=["scripts/a.py"], checks=CHECKS, log=LOG,
                                            verdict="UNVERIFIED", remote_sha=None, fetched_at=None,
                                            parity="NO-DATA: push exit 0 but the parity fetch failed (exit 128)")])
        item = [e for e in receipt["evidence"] if isinstance(e, dict)][0]
        self.assertEqual(item["verdict"], "UNVERIFIED")
        self.assertTrue(item["parity"].startswith("NO-DATA"), item)

    def test_no_landing_record_at_all_is_an_empty_scope_the_gate_refuses(self):
        receipt = self.write(record=False)
        self.assertEqual((receipt["scope"]["changed"], receipt["evidence"]), ([], []))
        self.assertFalse(receipt_door.require_per_file_checks(receipt["scope"]["changed"])[0])


if __name__ == "__main__":
    unittest.main()
