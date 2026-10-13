#!/usr/bin/env python3
"""RD1.a: the READY list is a contract between its writer and its reader, never truncated.

Run 1 of 2026-10-11 printed 8 READY paths then "... 85 more"; the pass landed only the 8 it could read and the
run ended ALARM UNPRODUCTIVE. The writer (pass_digest.py) now prints its READY block through one printer and can
say whether its header count equals its listed paths; the pass (loop_pass.sh) blocks with exit 44 when they
disagree; the lander (land_batch.py) refuses an ellipsis argument, a contract whose count and listed number
differ, a list longer than the listed paths, and a half given or unreadable contract.

Standard library only, Python 3.9 and 3.13, an empty HOME, no network. loop_pass.sh is read as text; its READY
contract block is cut out by its markers and run by bash against a HOME whose bin holds this checkout's
pass_digest.py, so the shell guard is exercised without running the pass.
Run: python3 -B scripts/loop/test_ready_contract.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import land_batch  # noqa: E402
import pass_digest  # noqa: E402

DIGEST = os.path.join(HERE, "pass_digest.py")
LANDER = os.path.join(HERE, "land_batch.py")
LOOP_PASS = os.path.join(HERE, "loop_pass.sh")
START, END = "# THE READY CONTRACT (RD1.a)", "# END OF THE READY CONTRACT"


def paths(n, prefix="N"):
    return ["/b/%s%d.a-r0-build.json" % (prefix, i) for i in range(1, n + 1)]


def clean_env(home, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BROTHER_", "GIT_", "PYTHON"))}
    env.update(HOME=home, **extra)
    return env


class Writer(unittest.TestCase):
    def test_ninety_ready_builds_print_every_path_and_the_count_matches(self):
        # the entry point, for real: main() over 90 READY builds of 90 sub units in the plan
        with tempfile.TemporaryDirectory(prefix="rd1-digest-") as d:
            plan = {"units": [{"id": "N%d" % i, "state": "PARTIAL", "sub_units": ["N%d.a" % i], "evidence": ""}
                              for i in range(1, 91)]}
            os.makedirs(os.path.join(d, "docs", "plan"))
            with open(os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w") as f:
                json.dump(plan, f)
            ev = os.path.join(d, "ev")
            for i in range(1, 91):
                run = os.path.join(ev, "unit-runs", "N%d.a-120000" % i)
                os.makedirs(run)
                with open(os.path.join(run, "STATUS"), "w") as f:
                    f.write("READY /b/N%d.a-r0-build.json\n" % i)
            r = subprocess.run([sys.executable, "-B", DIGEST, "--no-fetch"], cwd=d, capture_output=True, text=True,
                               timeout=120, env=clean_env(d, BROTHER_EVIDENCE=ev))
        lines = r.stdout.splitlines()
        self.assertIn("READY   90 to land:", lines, r.stdout + r.stderr)
        listed = [l.strip() for l in lines if l.startswith("        /") and l.endswith("-build.json")]
        self.assertEqual(sorted(listed), sorted(paths(90)))
        self.assertFalse(any("more" in l and l.lstrip().startswith("...") for l in lines))
        self.assertEqual(pass_digest.parse_ready(r.stdout), (90, sorted(paths(90))))
        self.assertEqual(pass_digest.ready_counts(r.stdout), ("90 90", 0))

    def test_the_one_printer_for_zero_one_eleven_and_ninety(self):
        self.assertEqual(pass_digest.ready_block([]), ["READY   0 to land"])
        for n in (1, 11, 90):
            block = pass_digest.ready_block(paths(n))
            self.assertEqual(block[0], "READY   %d to land:" % n)
            self.assertEqual(block[1:], ["        " + p for p in paths(n)])
            text = "\n".join(["TIME    x"] + block + ["UNPROBED 0"])
            self.assertEqual(pass_digest.parse_ready(text), (n, paths(n)))
            self.assertEqual(pass_digest.ready_counts(text), ("%d %d" % (n, n), 0))
        self.assertEqual(pass_digest.ready_counts("\n".join(pass_digest.ready_block([]))), ("0 0", 0))

    def test_a_truncated_digest_is_a_mismatch(self):
        text = "\n".join(["READY   93 to land:"] + ["        " + p for p in paths(8)] + ["        ... 85 more"])
        self.assertEqual(pass_digest.parse_ready(text), (93, paths(8)))
        self.assertEqual(pass_digest.ready_counts(text), ("93 8", 3))
        # a digest cut mid path line: the cut line fails the path pattern, so K is short of N
        cut = "\n".join(["READY   2 to land:", "        " + paths(2)[0], "        /b/N2.a-r0-bu"])
        self.assertEqual(pass_digest.ready_counts(cut), ("2 1", 3))

    def test_unknown_shapes_are_mismatches_not_zero(self):
        no_header = "\n".join(["        " + p for p in paths(2)])
        self.assertEqual(pass_digest.parse_ready(no_header), (None, paths(2)))
        self.assertEqual(pass_digest.ready_counts(no_header), ("- 2", 3))
        two = "\n".join(["READY   1 to land:", "        " + paths(1)[0], "READY   1 to land:"])
        self.assertEqual(pass_digest.parse_ready(two)[0], None)
        self.assertEqual(pass_digest.ready_counts(two)[1], 3)
        word = "\n".join(["READY   x to land:", "        " + paths(1)[0]])
        self.assertEqual(pass_digest.parse_ready(word)[0], None)
        self.assertEqual(pass_digest.ready_counts(word)[1], 3)
        self.assertEqual(pass_digest.ready_counts("READY   x to land")[1], 3)   # a header nobody can read, even with no paths

    def test_no_section_and_no_paths_is_not_a_mismatch(self):
        text = "TIME    x\nRUNS    NO-DATA: /r is unreadable (denied); READY, UNPROBED, NEEDFACT and STALLED are unknown, not zero\n" \
               "READY outside this run's scope, not offered to land: A.1\nREADY on closed units: 1 skipped: B.1"
        self.assertEqual(pass_digest.parse_ready(text), (None, []))
        self.assertEqual(pass_digest.ready_counts(text), ("- 0", 0))

    def test_the_ready_counts_verb_reads_stdin_and_exits_with_the_code(self):
        def verb(text):
            with tempfile.TemporaryDirectory(prefix="rd1-verb-") as d:
                return subprocess.run([sys.executable, "-B", DIGEST, "--ready-counts"], input=text, cwd=d,
                                      capture_output=True, text=True, timeout=60, env=clean_env(d))
        good = verb("\n".join(pass_digest.ready_block(paths(3))) + "\n")
        self.assertEqual((good.stdout.strip(), good.returncode), ("3 3", 0))
        bad = verb("READY   93 to land:\n" + "".join("        %s\n" % p for p in paths(8)) + "        ... 85 more\n")
        self.assertEqual((bad.stdout.strip(), bad.returncode), ("93 8", 3))


class Lander(unittest.TestCase):
    def run_lander(self, args, **extra):
        with tempfile.TemporaryDirectory(prefix="rd1-land-") as d:
            r = subprocess.run([sys.executable, "-B", LANDER] + args, cwd=d, capture_output=True, text=True,
                               timeout=120, env=clean_env(d, **extra))
            logs = os.path.isdir(os.path.join(d, ".claude", "evidence", "land-batch"))
        return r, logs

    def test_an_ellipsis_argument_is_refused_before_anything_is_touched(self):
        r, logs = self.run_lander(["/b/N5.a-r0-build.json", "..."])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("ellipsis", r.stdout)
        self.assertFalse(logs)
        for arg in ("...", "/b/...", "/b/N5.a...-build.json", "/b/N5.a-r0-build.json..."):
            self.assertIn("ellipsis", land_batch.ready_refusal(["/b/N1.a-r0-build.json", arg], env={}), arg)

    def test_count_not_equal_to_listed_is_refused(self):
        why = land_batch.ready_refusal(paths(2), env={"BROTHER_READY_COUNT": "93", "BROTHER_READY_LISTED": "8"})
        self.assertTrue(why and "93" in why and "8" in why, why)
        self.assertEqual(land_batch.ready_refusal(paths(2), env={"BROTHER_READY_COUNT": "8", "BROTHER_READY_LISTED": "8"}), "")
        r, logs = self.run_lander(paths(2), BROTHER_READY_COUNT="93", BROTHER_READY_LISTED="8")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("REFUSED: ") and "93" in r.stdout, r.stdout)
        self.assertFalse(logs)

    def test_a_longer_list_is_refused_and_a_deduplicated_shorter_one_is_not(self):
        env = {"BROTHER_READY_COUNT": "3", "BROTHER_READY_LISTED": "3"}
        self.assertIn("longer", land_batch.ready_refusal(paths(4), env=env))
        self.assertEqual(land_batch.ready_refusal(paths(3), env=env), "")
        self.assertEqual(land_batch.ready_refusal(paths(2), env=env), "")
        self.assertEqual(land_batch.ready_refusal(paths(90), env={"BROTHER_READY_COUNT": "90", "BROTHER_READY_LISTED": "90"}), "")
        self.assertEqual(land_batch.ready_refusal(paths(1), env={"BROTHER_READY_COUNT": "1", "BROTHER_READY_LISTED": "1"}), "")

    def test_a_half_given_or_unreadable_contract_is_refused(self):
        self.assertIn("half given", land_batch.ready_refusal(paths(1), env={"BROTHER_READY_COUNT": "1"}))
        self.assertIn("half given", land_batch.ready_refusal(paths(1), env={"BROTHER_READY_LISTED": "1"}))
        for bad in ("ninety", "-1", "", " ", "1.5", "+1"):
            for env in ({"BROTHER_READY_COUNT": bad, "BROTHER_READY_LISTED": bad},
                        {"BROTHER_READY_COUNT": bad, "BROTHER_READY_LISTED": "1"},
                        {"BROTHER_READY_COUNT": "1", "BROTHER_READY_LISTED": bad}):
                self.assertTrue(land_batch.ready_refusal(paths(1), env=env), (bad, env))

    def test_a_manual_run_without_the_contract_is_not_refused(self):
        self.assertEqual(land_batch.ready_refusal(paths(5), env={}), "")
        self.assertEqual(land_batch.ready_refusal(paths(5), env={"HOME": "/x"}), "")


class Pass(unittest.TestCase):
    def text(self):
        with open(LOOP_PASS, encoding="utf-8") as f:
            return f.read()

    def block(self):
        t = self.text()
        self.assertTrue(START in t and END in t, "loop_pass.sh has no READY contract block")
        return t[t.index(START):t.index(END)]

    def run_block(self, digest, with_digest=True, fake=None):
        with tempfile.TemporaryDirectory(prefix="rd1-pass-") as d:
            os.makedirs(os.path.join(d, ".claude", "bin"))
            if fake is not None:
                with open(os.path.join(d, ".claude", "bin", "pass_digest.py"), "w") as f:
                    f.write(fake)
            elif with_digest:
                for name in os.listdir(HERE):
                    if name.endswith(".py"):
                        os.symlink(os.path.join(HERE, name), os.path.join(d, ".claude", "bin", name))
            script = self.block() + '\necho "CONTRACT N=$READY_N K=$READY_K"\n'
            return subprocess.run(["/bin/bash", "-c", script], cwd=d, capture_output=True, text=True, timeout=60,
                                  env=clean_env(d, D=digest))

    def test_a_digest_whose_count_and_list_disagree_blocks_the_pass(self):
        t = self.text()
        self.assertLess(t.index("pass_digest exit=$DRC"), t.index(START))
        self.assertLess(t.index(END), t.index("land_batch.py $PICK); LB=$?"))
        bad = "READY   93 to land:\n" + "".join("        %s\n" % p for p in paths(8)) + "        ... 85 more"
        r = self.run_block(bad)
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertIn("LOOP BLOCKED: the digest's READY count and list disagree (93 8); nothing is landed", r.stdout)
        self.assertNotIn("CONTRACT", r.stdout)

    def test_an_agreeing_digest_hands_the_counts_on(self):
        r = self.run_block("\n".join(pass_digest.ready_block(paths(3))))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CONTRACT N=3 K=3", r.stdout)
        r = self.run_block("TIME    x\nRUNS    NO-DATA: unreadable")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CONTRACT N=0 K=0", r.stdout)

    def test_an_unreadable_digest_guard_blocks(self):
        r = self.run_block("\n".join(pass_digest.ready_block(paths(3))), with_digest=False)
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertIn("LOOP BLOCKED: the digest's READY count and list disagree", r.stdout)
        # an older deployed digest without the verb prints its whole text and exits 0: the counts cannot be read
        r = self.run_block("\n".join(pass_digest.ready_block(paths(3))), fake='print("TIME    x\\nREADY   3 to land:")\n')
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertIn("LOOP BLOCKED: the digest's READY counts could not be read", r.stdout)
        self.assertNotIn("CONTRACT", r.stdout)

    def test_the_landing_line_text_is_intact_and_carries_the_contract(self):
        t = self.text()
        self.assertTrue('LANDED_LINE=$(BROTHER_READY_COUNT="$READY_N" BROTHER_READY_LISTED="$READY_K" '
                        'python3 ~/.claude/bin/land_batch.py $PICK); LB=$?' in t, "the landing line lost the contract")
        self.assertEqual(t.count("land_batch.py $PICK); LB=$?"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
