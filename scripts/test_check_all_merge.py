"""check_all.sh --shard / --dry and scripts/check_all_merge.py, unit CA1.

The real battery is never run here (about two hours): the shard cases list the
battery with --dry, which runs no check, and the merge cases feed hand written
shard outputs. Everything outside the test's own temporary folder is injected;
HOME is never read, no network, no model, no ps.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECK_ALL = os.path.join(HERE, "check_all.sh")
sys.path.insert(0, HERE)
import check_all_merge as M  # noqa: E402

REG_RE = re.compile(r'\s*run_check\s+"([^"]+)"\s+(.*)')   # system_doc.py's own reader


def _env(tmp, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in ("BROTHER_LONG_CHECKS", "BROTHER_HEAVY_SLOT_HELD")}
    env.update({"HOME": tmp, "TMPDIR": tmp, "BROTHER_HEAVY_SLOT": "off"})
    env.update(extra)
    return env


def run_battery(args, script=CHECK_ALL, **extra):
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(["sh", script] + list(args), cwd=tmp, env=_env(tmp, **extra),
                              capture_output=True, text=True, timeout=300)
    return proc


def dry_names(stdout):
    return [line[8:].strip() for line in stdout.splitlines() if line.startswith("DRY     ")]


def shard_text(k, n, rows, total, owned=None, ran=None, commit="abc123", reach=False, summary=True):
    """One shard's saved output; rows is [(verdict, name)] in sequence order."""
    out = ["Brother: measuring commit %s (v1.1.2-5-gabc)" % commit, "",
           "Brother: every shipped check, each reporting its own exit code", ""]
    counts = {"PASS": 0, "FAIL": 0, "NO-DATA": 0}
    failed, nodata = [], []
    for verdict, name in rows:
        counts[verdict] += 1
        code = {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[verdict]
        out.append("%-7s exit %-3s %-34s %s" % (verdict, code, name, "last line"))
        if verdict == "FAIL":
            failed.append(name)
            out.append("        FAIL: test_x (module.Case)")
        if verdict == "NO-DATA":
            nodata.append(name)
    owned = len(rows) if owned is None else owned
    ran = len(rows) if ran is None else ran
    if not summary:
        return "\n".join(out) + "\n"
    out += ["", "SHARD %d/%d: owned %d of %d rows, ran %d%s" % (k, n, owned, total, ran,
                                                              " (reach)" if reach else ""),
            "pass %d   fail %d   no-data %d" % (counts["PASS"], counts["FAIL"], counts["NO-DATA"])]
    if failed:
        out.append("FAILED: " + " ".join(failed))
    if nodata:
        out.append("NO-DATA: " + " ".join(nodata) + "  (not a pass, and not a failure)")
    return "\n".join(out) + "\n"


def four_shards(fail_in=None, nodata_in=None):
    names = ["row%02d" % i for i in range(1, 13)]
    texts = []
    for k in range(1, 5):
        rows = []
        for i, name in enumerate(names, 1):
            if (i - 1) % 4 == k - 1:
                verdict = "FAIL" if name == fail_in else "NO-DATA" if name == nodata_in else "PASS"
                rows.append((verdict, name))
        texts.append(shard_text(k, 4, rows, 12))
    return texts


class Merge(unittest.TestCase):

    def test_four_complete_shards_merge_to_a_pass(self):
        code, report = M.merge(four_shards())
        self.assertEqual(code, 0, report)
        self.assertIn("MERGED pass 12   fail 0   no-data 0 from 4 shard(s)", report)
        self.assertTrue(report.rstrip().endswith("VERDICT PASS"), report)

    def test_a_missing_shard_is_no_data_never_a_pass(self):
        code, report = M.merge(four_shards()[:3])
        self.assertEqual(code, 2, report)
        self.assertIn("3 shard file(s) for a 4 shard run", report)
        self.assertTrue(report.rstrip().endswith("VERDICT NO-DATA"), report)
        self.assertEqual(M.merge([])[0], 2)

    def test_a_truncated_shard_output_is_no_data(self):
        texts = four_shards()
        texts[2] = texts[2].split("\nSHARD ")[0] + "\n"          # stopped before its summary
        self.assertIsNone(M.parse_shard(texts[2]))
        self.assertEqual(M.merge(texts)[0], 2)
        texts = four_shards()
        texts[1] = shard_text(2, 4, [("PASS", "row02")], 12, owned=3, ran=3)   # 1 row line, ran 3
        self.assertIsNone(M.parse_shard(texts[1]))
        self.assertEqual(M.merge(texts)[0], 2)
        texts = four_shards()
        texts[0] = None                                            # unreadable file
        self.assertEqual(M.merge(texts)[0], 2)

    def test_inconsistent_shards_are_no_data(self):
        base = four_shards()
        dup = list(base)
        dup[3] = base[3].replace("SHARD 4/4", "SHARD 3/4")
        self.assertIn("not exactly 1 to 4", M.merge(dup)[1], "duplicate k")
        other_t = list(base)
        other_t[0] = base[0].replace("of 12 rows", "of 13 rows")
        self.assertIn("disagree on the row count", M.merge(other_t)[1], "different T")
        other_n = [shard_text(1, 2, [("PASS", "a")], 2), shard_text(2, 3, [("PASS", "b")], 2)]
        self.assertIn("disagree on n", M.merge(other_n)[1], "different n")
        short = [shard_text(1, 2, [("PASS", "a")], 3), shard_text(2, 2, [("PASS", "b")], 3)]
        self.assertIn("own 2 of 3 rows", M.merge(short)[1], "owned sum is not T")
        skipped = list(base)
        skipped[1] = shard_text(2, 4, [("PASS", "row02"), ("PASS", "row06")], 12, owned=3)
        self.assertIn("ran 2 of the 3 rows", M.merge(skipped)[1], "ran differs from owned")
        commits = list(base)
        commits[2] = base[2].replace("commit abc123", "commit def456")
        self.assertIn("different commits", M.merge(commits)[1], "two commits")
        mixed = list(base)
        mixed[0] = base[0].replace("ran 3\n", "ran 3 (reach)\n")
        self.assertIn("some shards ran under --reach", M.merge(mixed)[1], "reach mixed")
        for case in (dup, other_t, other_n, short, skipped, commits, mixed):
            code, report = M.merge(case)
            self.assertEqual(code, 2, report)
            self.assertTrue(report.rstrip().endswith("VERDICT NO-DATA"), report)

    def test_a_fail_in_one_shard_fails_the_merge_and_names_it(self):
        code, report = M.merge(four_shards(fail_in="row07", nodata_in="row10"))
        self.assertEqual(code, 1, report)
        self.assertIn("FAILED: row07", report)
        self.assertIn("NO-DATA: row10  (not a pass, and not a failure)", report)
        self.assertTrue(report.rstrip().endswith("VERDICT FAIL"), report)
        code, report = M.merge(four_shards(nodata_in="row10"))
        self.assertEqual(code, 0, report)
        self.assertIn("NO-DATA: row10", report)

    def test_the_merged_log_parses_like_one_run(self):
        import battery_verdict
        texts = four_shards(fail_in="row03")
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for k, text in enumerate(texts, 1):
                path = os.path.join(tmp, "shard-%d.txt" % k)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                files.append(path)
            log = os.path.join(tmp, "merged.txt")
            with redirect_stdout(StringIO()):
                code = M.main(["merge", "--log", log] + files)
                nodata_code = M.main(["merge", "--log", os.path.join(tmp, "nodata.txt")] + files[:3])
                absent_code = M.main(["merge", files[0], os.path.join(tmp, "absent.txt"), files[2], files[3]])
            with open(log, encoding="utf-8") as fh:
                merged = fh.read()
            self.assertEqual(nodata_code, 2)
            self.assertFalse(os.path.exists(os.path.join(tmp, "nodata.txt")), "a NO-DATA merge writes no log")
            self.assertEqual(absent_code, 2)
        self.assertEqual(code, 1)
        rows = battery_verdict.parse_check_all_output(merged)
        self.assertEqual(sorted(r[0] for r in rows), ["row%02d" % i for i in range(1, 13)])
        self.assertEqual([r for r in rows if r[1] == "FAIL"], [("row03", "FAIL", ["test_x"])])
        self.assertEqual(battery_verdict.parse_commit(merged)["sha"], "abc123")
        self.assertIn("pass 11   fail 1   no-data 0", merged)


class Shards(unittest.TestCase):
    """The real check_all.sh, listed with --dry: no check runs."""

    @classmethod
    def setUpClass(cls):
        cls.full = run_battery(["--dry"])
        cls.shards = [run_battery(["--shard", "%d/4" % k, "--dry"]) for k in range(1, 5)]

    def test_the_four_shards_partition_the_battery_round_robin(self):
        self.assertEqual(self.full.returncode, 0, self.full.stdout[-2000:] + self.full.stderr)
        full = dry_names(self.full.stdout)
        self.assertGreater(len(full), 100)
        lists = [dry_names(p.stdout) for p in self.shards]
        for k, names in enumerate(lists, 1):
            self.assertEqual(names, [n for i, n in enumerate(full, 1) if (i - 1) % 4 == k - 1],
                             "shard %d is not round robin" % k)
            self.assertIn("SHARD %d/4: owned %d of %d rows, ran 0" % (k, len(names), len(full)),
                          self.shards[k - 1].stdout)
        self.assertEqual(sorted(sum(lists, [])), sorted(full))
        self.assertNotIn("SHARD", self.full.stdout, "an unsharded run prints nothing new")

    def test_registration_lines_keep_their_shape(self):
        with open(CHECK_ALL, encoding="utf-8") as fh:
            text = fh.read()
        regs = [m.group(1) for m in (REG_RE.match(l) for l in text.splitlines()) if m]
        for line in text.splitlines():
            if 'run_check "' in line and not line.lstrip().startswith("#"):
                self.assertTrue(REG_RE.match(line), line)
            if "shard_take" in line:
                self.assertIsNone(REG_RE.match(line), line)
        # without BROTHER_LONG_CHECKS the long row is listed once by its else branch
        self.assertEqual(sorted(dry_names(self.full.stdout)), sorted(regs))

    def test_the_long_check_row_is_in_exactly_one_shard(self):
        owners = [k for k, p in enumerate(self.shards, 1)
                  if "release-note-perturb" in dry_names(p.stdout)]
        self.assertEqual(len(owners), 1, owners)
        on = [run_battery(["--shard", "%d/4" % k, "--dry"], BROTHER_LONG_CHECKS="1") for k in range(1, 5)]
        owners_on = [k for k, p in enumerate(on, 1) if "release-note-perturb" in dry_names(p.stdout)]
        self.assertEqual(owners_on, owners)

    def test_dry_skips_the_prelude_and_runs_nothing(self):
        proc = run_battery(["--dry", "--shard", "16/16"])
        self.assertEqual(proc.returncode, 0, proc.stdout[-1000:] + proc.stderr)
        self.assertIn("SHARD 16/16: owned", proc.stdout)
        self.assertNotIn("PASS ", proc.stdout)
        self.assertNotRegex(proc.stdout + proc.stderr, r"temp residue|location guard|REFUSED")

    def test_bad_shard_spec_exits_2(self):
        for args in (["--shard", "0/4"], ["--shard", "5/4"], ["--shard", "1/17"], ["--shard", "a/b"],
                     ["--shard", "4"], ["--shard", "1/2/3"], ["--shard"], ["--bogus"],
                     ["--reach", "-x"], ["--reach", "a b"]):
            proc = run_battery(args + ["--dry"])
            self.assertEqual(proc.returncode, 2, (args, proc.stdout, proc.stderr))
            self.assertNotIn("DRY", proc.stdout, args)
            self.assertNotIn("measuring commit", proc.stdout, args)
            self.assertIn("usage:", proc.stderr, args)


FIXTURE_BATTERY = """#!/bin/sh
run_check "alpha"      python3 scripts/test_alpha.py
run_check "beta"       python3 scripts/test_beta.py
run_check "gate"       sh scripts/test_gate.sh
run_check "nowhere"    python3 -m unittest nowhere.module
run_check "gamma-self" python3 -B scripts/gamma.py --selftest
run_check "delta-self" /usr/bin/python3 scripts/delta.py --selftest
run_check "epsilon-self" python3 scripts/epsilon.py --selftest
"""
FIXTURE_FILES = {
    "scripts/alpha.py": "VALUE = 1\n",
    "scripts/test_alpha.py": "import unittest\nimport alpha\n",
    "scripts/beta.py": "VALUE = 2\n",
    "scripts/test_beta.py": "import unittest\nimport beta\n",
    "scripts/gamma.py": "import sys\nif sys.argv[1:] == ['--selftest']:\n    pass\n",
    "scripts/delta.py": "import sys\nif sys.argv[1:] == ['--selftest']:\n    pass\n",
    "scripts/test_gate.sh": "exit 0\n",
    "scripts/epsilon.py": "import sys\nimport alpha\nif sys.argv[1:] == ['--selftest']:\n    pass\n",
    "scripts/check_all.sh": FIXTURE_BATTERY,
    "scripts/zeta.py": "VALUE = 4\n",
    "tests/test_omega.py": "import unittest\n",
}


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"] + list(args),
                   cwd=root, env=_env(root), check=True, capture_output=True, text=True, timeout=60)


def _write(root, files):
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)


class Reach(unittest.TestCase):
    """reach_rows over a two commit fixture repository: the second commit
    changes alpha.py and gamma.py; epsilon.py imports alpha."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls._tmp.name)
        _write(cls.root, FIXTURE_FILES)
        _git(cls.root, "init", "-q")
        _git(cls.root, "add", "-A")
        _git(cls.root, "commit", "-q", "-m", "one")
        _git(cls.root, "tag", "one")
        _write(cls.root, {"scripts/alpha.py": "VALUE = 3\n",
                          "scripts/gamma.py": FIXTURE_FILES["scripts/gamma.py"] + "# two\n"})
        _git(cls.root, "commit", "-q", "-am", "two")
        _git(cls.root, "tag", "two")
        _git(cls.root, "rm", "-q", "scripts/zeta.py")
        _git(cls.root, "commit", "-q", "-m", "three")
        _git(cls.root, "tag", "three")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_row_that_cannot_be_mapped_runs_under_reach(self):
        names, why = M.reach_rows(self.root, "one..two")
        self.assertIsNotNone(names, why)
        self.assertIn("alpha", names)
        self.assertIn("gate", names)
        self.assertIn("nowhere", names)
        self.assertNotIn("beta", names)
        self.assertEqual(why, "REACH 5 of 7 row(s) from 2 changed path(s)")

    def test_a_selftest_row_runs_when_its_module_changed(self):
        names, _ = M.reach_rows(self.root, "one..two")
        self.assertIn("gamma-self", names)       # its own file changed
        self.assertIn("epsilon-self", names)     # unchanged, but it imports a changed module
        self.assertNotIn("delta-self", names)

    def test_an_empty_range_runs_only_the_rows_it_cannot_judge(self):
        names, why = M.reach_rows(self.root, "two..two")
        self.assertEqual(sorted(names), ["gate", "nowhere"])
        self.assertIn("from 0 changed path(s)", why)

    def test_a_reach_that_cannot_be_computed_runs_every_row(self):
        names, why = M.reach_rows(self.root, "two..three")
        self.assertIsNone(names, why)
        self.assertIn("deletes scripts/zeta.py", why)
        for rng in ("no-such-rev..HEAD", "-x", "a b"):
            names, why = M.reach_rows(self.root, rng)
            self.assertIsNone(names, rng)
            out = StringIO()
            with redirect_stdout(out):
                code = M.main(["reach", "--range", rng, "--root", self.root])
            self.assertEqual(code, 2, rng)
            self.assertIn("REACH NO-DATA:", out.getvalue())
            self.assertIn("running every row", out.getvalue())
        names, why = M.reach_rows(self.root, "one..two", battery_path="scripts/absent.sh")
        self.assertIsNone(names, why)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(M.main(["reach", "--range", "one..two", "--root", self.root]), 0)
        self.assertEqual(out.getvalue().splitlines()[-1], "REACH 5 of 7 row(s) from 2 changed path(s)")

    def test_row_selected_runs_every_row_it_cannot_judge(self):
        sel = lambda cmd: M.row_selected("r", cmd, ["scripts/test_alpha.py"], ["scripts/alpha.py"], self.root)
        self.assertTrue(sel('python3 scripts/test_beta.py "$X"'))
        self.assertTrue(sel("python3 scripts/test_beta.py && true"))
        self.assertTrue(sel("python3 scripts/test_beta.py | tail -1"))
        self.assertTrue(sel("python3 'scripts/test_beta.py"))           # shlex cannot split it
        self.assertTrue(sel("bash scripts/test_beta.py"))
        self.assertTrue(sel("sh scripts/test_gate.sh"))
        self.assertTrue(sel("python3 scripts/gamma.py"))                  # a live check, not a test
        self.assertTrue(sel("python3 tests/test_omega.py"))               # outside the reach directories
        self.assertTrue(sel("python3 -B scripts/test_alpha.py -v"))
        self.assertTrue(sel("python3 scripts/alpha.py --selftest"))      # its own file changed
        self.assertFalse(sel("python3 scripts/test_beta.py -v"))
        self.assertFalse(sel("python3 -m unittest scripts.test_beta"))
        self.assertFalse(sel("python3 scripts/delta.py --selftest"))
        self.assertEqual(M.rows_of(FIXTURE_BATTERY)[3], ("nowhere", "python3 -m unittest nowhere.module"))

    def test_shard_and_reach_compose(self):
        full = dry_names(run_battery(["--dry"]).stdout)
        chosen = full[2::7]
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "scripts"))
            with open(CHECK_ALL, encoding="utf-8") as src, \
                    open(os.path.join(tmp, "scripts", "check_all.sh"), "w", encoding="utf-8") as dst:
                dst.write(src.read())
            fake = os.path.join(tmp, "scripts", "check_all_merge.py")

            def fake_reach(code, names):
                with open(fake, "w", encoding="utf-8") as fh:
                    fh.write("import sys\nprint(%r)\nprint('REACH fake')\nsys.exit(%d)\n"
                             % ("\n".join(names), code))

            script = os.path.join(tmp, "scripts", "check_all.sh")
            fake_reach(0, chosen)
            reach = run_battery(["--dry", "--reach", "a..b"], script=script)
            self.assertEqual(dry_names(reach.stdout), chosen)
            shards = [run_battery(["--dry", "--reach", "a..b", "--shard", "%d/4" % k], script=script)
                      for k in range(1, 5)]
            for k, proc in enumerate(shards, 1):
                own = [n for i, n in enumerate(full, 1) if (i - 1) % 4 == k - 1]
                self.assertEqual(dry_names(proc.stdout), [n for n in own if n in chosen])
                self.assertIn("SHARD %d/4: owned %d of %d rows, ran 0 (reach)" % (k, len(own), len(full)),
                              proc.stdout)
            fake_reach(2, chosen)
            fallback = run_battery(["--dry", "--reach", "a..b"], script=script)
            self.assertEqual(dry_names(fallback.stdout), full)
            self.assertIn("running every row", fallback.stdout)
            leftovers = [n for n in os.listdir(tmp) if n.startswith("check-all-reach")]
        self.assertEqual(leftovers, [], "the reach list is removed on exit")

    def test_the_runbook_names_the_shard_merge_and_reach_commands(self):
        path = os.path.join(ROOT, "docs", "plan", "specs", "RUNBOOK-1.1.3.md")
        if not os.path.isfile(path):
            self.skipTest("maintainer only runbook absent from this tree")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue("\n## CA1 " in text, "the runbook has no CA1 section")
        section = text.split("\n## CA1 ", 1)[1].split("\n## ", 1)[0]
        for needle in ("sh scripts/check_all.sh --shard $k/4",
                       "python3 scripts/check_all_merge.py merge --log",
                       "sh scripts/check_all.sh --reach "):
            self.assertTrue(needle in section, needle)




class TheRowNamesAreUnique(unittest.TestCase):
    """Reach and shard select rows by name: a name registered twice drags its twin in (four existed on 2026-10-12)."""
    def test_no_run_check_name_is_registered_twice(self):
        import collections
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_all.sh"), encoding="utf-8") as fh:
            names = re.findall(r'^run_check "([^"]+)"', fh.read(), re.M)
        dup = sorted(n for n, c in collections.Counter(names).items() if c > 1)
        self.assertEqual(dup, [])

if __name__ == "__main__":
    unittest.main()
