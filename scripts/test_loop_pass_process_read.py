#!/usr/bin/env python3
"""loop_pass.sh reads the process table as a fact or not at all (orchestrator item from Lane L2, 2026-09-27).

The pass counts live unit runners twice: after a refused landing, and in its own productivity check at the end. Both
read `ps -eo command | grep -c`, which ignores ps's exit status, so a failed or refused read counted as zero live
runners, and zero is what turns a quiet pass into exit 45 UNPRODUCTIVE, which ends the run. The same defect class Lane
L2 fixed in loop_done.py (finding 9): a read that exits nonzero, or a table with no row past its header (a real one
always lists ps itself), is NO-DATA. The pass then says so and exits 0: the verdict holds, the next pass reads again.

Driven through the real loop_pass.sh with the stubs of scripts/test_loop_pass_code_root.py and a `ps` stub first on
PATH. One condition per case: the shape of the process read. Each site has its control: a real table with no runner
still exits 45 (a true zero), and a table naming a runner exits 0.
Run: python3 -B scripts/test_loop_pass_process_read.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_loop_pass_code_root as P  # noqa: E402  the pass harness: scratch tree, code root, bin stubs

TABLE = ["1 0 1 /sbin/launchd", "77 1 77 /bin/bash loop_pass.sh", "99 1 99 ps -ax -ww -o pid=,ppid=,pgid=,command="]
# The fixture home is the placeholder "you": scripts/test_export_public.py refuses any other home-anchored .claude path in a shipped file.
# A runner is the loop's only from the deployed bin copy, <HOME>/.claude/bin (the harness's self.bin), or a run of this loop.
RUNNER = "4242 1 4242 /usr/bin/python3 %s/unit_runner.py U1 s1"
VIEWER = "41001 1 41001 python3 /fixture/viewer.py /fixture/bin/unit_runner.py X X.1"


def table_body(rows):
    """A readable `ps` stub: rows ("pid ppid pgid command") whole for loop_procs' read (-o pid=,ppid=,pgid=,command=), a
    COMMAND column for a `ps -eo command` read, so one fixture drives either reader."""
    t = "".join(r + "\n" for r in rows)
    return "T='%s'\ncase \"$*\" in *pid=*) printf '%%s' \"$T\";; *) echo COMMAND; printf '%%s' \"$T\" | LC_ALL=C cut -d' ' -f4-;; esac\nexit 0\n" % t
READS = {                                   # label: ps stub body
    "refused": 'echo "ps: operation not permitted" >&2\nexit 1\n',
    "refused after printing": 'printf "COMMAND\\n/usr/bin/python3 unit_runner.py U1 s1\\n"\nexit 1\n',
    "header only": 'echo "COMMAND"\nexit 0\n',
    "empty": "exit 0\n",
}


class ProcessRead(unittest.TestCase):
    setUp = P.PassCodeRoot.setUp
    tearDown = P.PassCodeRoot.tearDown
    run_pass = P.PassCodeRoot.run_pass
    read = P.PassCodeRoot.read

    def pass_with(self, ps_body, landing="nothing"):
        """The pass lands nothing (or its landing is refused), closes nothing and starts nothing, so only the live
        runner count can decide it; `ps_body` is the process read it sees."""
        shim = os.path.join(self.box, "shim")
        P.w(os.path.join(shim, "ps"), "#!/bin/bash\n" + ps_body, 0o755)
        P.w(os.path.join(self.bin, "runner_pool.py"), 'print("POOL nothing to start")\n', 0o755)
        P.w(os.path.join(self.bin, "land_batch.py"),
            'print("nothing landed")\n' if landing == "nothing" else 'import sys\nprint("REFUSED: gates red")\nsys.exit(1)\n', 0o755)
        return self.run_pass(BROTHER_CODE_ROOT=self.code, PATH=shim + os.pathsep + os.environ.get("PATH", ""))

    def test_an_unreadable_table_is_no_data_at_the_productivity_check(self):
        for label, body in READS.items():
            with self.subTest(label):
                r = self.pass_with(body)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("NO-DATA: the process table could not be read", r.stdout)
                self.assertNotIn("PASS DID NOTHING", r.stdout)

    def test_an_unreadable_table_is_no_data_after_a_refused_landing(self):
        for label, body in READS.items():
            with self.subTest(label):
                r = self.pass_with(body, landing="refused")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("NO-DATA: the process table could not be read", r.stdout)
                self.assertNotIn("PASS DID NOTHING", r.stdout)

    def test_a_readable_table_with_no_runner_is_a_true_zero(self):
        """The control: a real table naming no runner still ends the pass 45, at both sites."""
        for landing in ("nothing", "refused"):
            with self.subTest(landing):
                r = self.pass_with(table_body(TABLE), landing=landing)
                self.assertEqual(r.returncode, 45, r.stdout + r.stderr)
                self.assertIn("PASS DID NOTHING", r.stdout)
                self.assertNotIn("NO-DATA", r.stdout)

    def test_a_readable_table_with_a_runner_is_a_live_pass(self):
        """The control: a table naming a runner is a quiet pass, exit 0, at both sites."""
        body = table_body(TABLE + [RUNNER % self.bin])
        for landing in ("nothing", "refused"):
            with self.subTest(landing):
                r = self.pass_with(body, landing=landing)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("PASS DID NOTHING", r.stdout)
                self.assertNotIn("NO-DATA", r.stdout)

    def test_a_process_that_only_names_a_runner_is_not_one(self):
        """X2 finding 7: loop_procs owns a loop tool only at the program position, so a stop never signals a viewer
        whose ARGUMENT names bin/unit_runner.py; this count read any command line mentioning the file, so that viewer
        kept a pass that did nothing from exiting 45 after every runner had gone. Both sites."""
        for landing in ("nothing", "refused"):
            with self.subTest(landing):
                r = self.pass_with(table_body(TABLE + [VIEWER]), landing=landing)
                self.assertEqual(r.returncode, 45, r.stdout + r.stderr)
                self.assertIn("PASS DID NOTHING", r.stdout)

    def reader(self, body):
        """The deployed process reader replaced by `body`: one shape of a reader that cannot be believed."""
        P.w(os.path.join(self.bin, "loop_procs.py"), body, 0o755)

    def test_a_reader_that_fails_is_no_data_whatever_it_printed(self):
        """Its exit decides: a count printed by a reader that exits 2 is not a count."""
        for landing in ("nothing", "refused"):
            with self.subTest(landing):
                self.reader("print(0)\nraise SystemExit(2)\n")
                r = self.pass_with(table_body(TABLE), landing=landing)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("NO-DATA: the process table could not be read", r.stdout)

    def test_a_reader_that_prints_no_count_is_no_data(self):
        """An exit 0 with no number (an empty or broken deploy of the reader) is not zero runners."""
        for landing in ("nothing", "refused"):
            with self.subTest(landing):
                self.reader("")
                r = self.pass_with(table_body(TABLE), landing=landing)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("NO-DATA: the process table could not be read", r.stdout)


if __name__ == "__main__":
    unittest.main()
