#!/usr/bin/env python3
"""RC0.a (docs/plan/specs/RC0.md): the release candidate handover checker.

Fixtures are a throwaway evidence folder of small source files and a throwaway
git repository holding one commit, a two unit WBS and one test file. The real
handover case runs only once docs/handover/2026-10-12/HANDOVER-1.1.3-RC.md
exists (skipped as NO-DATA before); the runbook case only where the runbook
ships. Nothing here starts ps, reaches the network, tags or pushes.

Python 3.9, standard library only. No em or en dashes.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rc_handover as R  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
REAL_HANDOVER = os.path.join(ROOT, "docs", "handover", "2026-10-12", "HANDOVER-1.1.3-RC.md")
RUNBOOK = os.path.join(ROOT, "docs", "plan", "specs", "RUNBOOK-1.1.3.md")
SOURCES = {
    "gate.txt": "pass 59   fail 0   no-data 0\n",
    "host.txt": "probe\nGREEN installed brother 1.1.3\n",
    "resume.txt": "replay\nGREEN resume lock corruption\n",
    "sut.txt": "NO-DATA productive units 0, upper bound 40 minutes\n",
    "bench.txt": "COMPOSITE: refused (two controls missing)\n",
    "manifest.txt": "REHEARSAL READY 1.1.3 abcdef012345 exit 0 record /ev/r.json\n",
}


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True,
                         check=True, env=GIT_ENV)
    return out.stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rc-handover-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        self.ev = os.path.join(self.tmp, "ev")
        os.makedirs(os.path.join(self.root, "docs", "plan"))
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(self.ev)
        ids = ["CD1", "LX1"] + ["U%02d" % i for i in range(40)]
        with open(os.path.join(self.root, "docs", "plan", "BROTHER-1.1.3-WBS.json"), "w") as f:
            json.dump({"units": [{"id": i} for i in ids]}, f)
        with open(os.path.join(self.root, "scripts", "test_x.py"), "w") as f:
            f.write("pass\n")
        git(self.tmp, "init", "-q", self.root)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "candidate")
        self.sha = git(self.root, "rev-parse", "HEAD")
        self.blob = git(self.root, "rev-parse", "HEAD:scripts/test_x.py")
        for name, text in SOURCES.items():
            self.write_source(name, text)
        self.fields = {
            "Baseline SHA/tag": "%s (v1.1.2)" % self.sha,
            "Candidate SHA/branch": "%s on build/113" % self.sha[:9],
            "Landed U-items and linked tests": "CD1: python3 -B scripts/test_x.py; LX1: python3 -B scripts/test_x.py",
            "Reverted/refused items and reasons": "U01: refused, the spec contradicted the code",
            "Public gate summary": "PASS pass 59 fail 0 no-data 0 (source=gate.txt)",
            "Actual signed-in host results, installed identity and guard action": "PASS host green (source=host.txt)",
            "Resume/lock/corruption replay": "PASS replay green (source=resume.txt)",
            "SUT productive units, minutes and interventions": "NO-DATA upper bound only (source=sut.txt)",
            "Raw competitive arm packets and missing controls": "NO-DATA composite refused (source=bench.txt)",
            "Version/source-to-bundle/export manifest check": "PASS rehearsal READY (source=manifest.txt)",
            "Unresolved risks that block stronger claims": "the SUT bound is an upper bound only",
            "Owner decision required": "PENDING: accept, hold or abandon. NO AUTO RELEASE.",
        }

    def write_source(self, name, text, data=None):
        p = os.path.join(self.ev, name)
        with open(p, "wb") as f:
            f.write(data if data is not None else text.encode("utf-8"))
        return p

    def render(self, fields=None, extra=""):
        fields = self.fields if fields is None else fields
        lines = ["# Handover 1.1.3 RC", ""]
        for k, v in (fields.items() if isinstance(fields, dict) else fields):
            lines.append("- %s: %s" % (k, v))
        return "\n".join(lines) + "\n" + extra

    def check(self, fields=None, extra="", data=None):
        p = os.path.join(self.tmp, "HANDOVER.md")
        with open(p, "wb") as f:
            f.write(data if data is not None else self.render(fields, extra).encode("utf-8"))
        return R.check_handover(p, self.root, self.ev)

    def with_field(self, label, value):
        f = dict(self.fields)
        f[label] = value
        return f

    def assertRefused(self, problems, needle):
        self.assertTrue(problems, "expected a refusal naming %r, got none" % needle)
        self.assertTrue(any(needle in p for p in problems), "no problem names %r: %r" % (needle, problems))


class Structure(Base):
    def test_complete_handover_passes(self):  # REQ-RC0-01
        self.assertEqual(self.check(), [])

    def test_each_missing_field_is_named(self):  # REQ-RC0-02, twelve single faults
        for label in R.FIELDS:
            f = dict(self.fields)
            del f[label]
            self.assertRefused(self.check(f), "missing field: %s" % label)

    def test_repeated_field(self):
        pairs = list(self.fields.items()) + [("Unresolved risks that block stronger claims", "again")]
        self.assertRefused(self.check(pairs), "repeated field: Unresolved risks")

    def test_unrecognized_field(self):
        self.assertRefused(self.check(extra="- Mood of the night: fine\n"), "unrecognized field: Mood of the night")

    def test_empty_field(self):
        self.assertRefused(self.check(self.with_field("Unresolved risks that block stronger claims", "")),
                           "empty field: Unresolved risks")

    def test_out_of_order(self):
        pairs = list(self.fields.items())
        pairs[0], pairs[1] = pairs[1], pairs[0]
        self.assertRefused(self.check(pairs), "out of order")

    def test_empty_file_names_all_twelve(self):
        problems = self.check(data=b"")
        self.assertEqual(sum(1 for p in problems if p.startswith("missing field")), 12)

    def test_only_one_field(self):
        problems = self.check([("Owner decision required", self.fields["Owner decision required"])])
        self.assertEqual(sum(1 for p in problems if p.startswith("missing field")), 11)

    def test_not_utf8_is_refused(self):
        self.assertRefused(self.check(data=b"- Baseline SHA/tag: \xff\xfe\n"), "not UTF-8")


class Verdicts(Base):
    def test_source_verdict_rules(self):
        sv = R.source_verdict
        self.assertEqual(sv("pass 59   fail 1   no-data 0\n"), "FAIL")
        self.assertEqual(sv("pass 59   fail 0   no-data 2\n"), "NO-DATA")
        self.assertEqual(sv("pass 59   fail 0   no-data 0\n"), "PASS")
        self.assertEqual(sv("REHEARSAL NOT-READY 1 a exit 1\nREHEARSAL READY 1 a exit 0\n"), "PASS")
        self.assertEqual(sv("REHEARSAL READY 1 a exit 0\nREHEARSAL NOT-READY 1 a exit 1\n"), "FAIL")
        self.assertEqual(sv("GREEN x\n"), "PASS")
        self.assertEqual(sv("RED x\n"), "FAIL")
        self.assertEqual(sv("COMPOSITE: refused\n"), "NO-DATA")
        self.assertEqual(sv("bound: upper bound 30\n"), "NO-DATA")
        self.assertEqual(sv("NO-DATA none\n"), "NO-DATA")
        self.assertEqual(sv("BLOCKED seat\n"), "BLOCKED")
        self.assertEqual(sv("hello\n"), "NO-DATA")
        self.assertEqual(sv(""), "NO-DATA")
        # A torn last line never decides: the last whole line does.
        self.assertEqual(sv("RED x\nGREEN y"), "FAIL")

    def test_value_verdict(self):
        self.assertEqual(R.value_verdict("PASS all green"), "PASS")
        self.assertEqual(R.value_verdict("GREEN host"), "NO-DATA")
        self.assertEqual(R.value_verdict("see below, BLOCKED by a seat"), "BLOCKED")
        self.assertEqual(R.value_verdict(""), "NO-DATA")

    def test_pass_beside_no_data_source_is_refused(self):  # REQ-RC0-03
        f = self.with_field("SUT productive units, minutes and interventions", "PASS units counted (source=sut.txt)")
        self.assertRefused(self.check(f), "says PASS but its source says NO-DATA")

    def test_no_data_beside_pass_source_is_allowed(self):
        f = self.with_field("Actual signed-in host results, installed identity and guard action",
                            "NO-DATA not claimed yet (source=host.txt)")
        self.assertEqual(self.check(f), [])

    def test_counts_must_match(self):  # REQ-RC0-04
        self.write_source("gate.txt", "pass 59   fail 1   no-data 0\n")
        f = self.with_field("Public gate summary", "FAIL pass 59 fail 0 no-data 0 (source=gate.txt)")
        self.assertRefused(self.check(f), "counts")

    def test_counts_missing_from_value_refused(self):
        f = self.with_field("Public gate summary", "PASS all good (source=gate.txt)")
        self.assertRefused(self.check(f), "counts")

    def test_fail_source_never_summarized_pass(self):
        self.write_source("gate.txt", "pass 59   fail 1   no-data 0\n")
        f = self.with_field("Public gate summary", "PASS pass 59 fail 1 no-data 0 (source=gate.txt)")
        self.assertRefused(self.check(f), "says PASS but its source says FAIL")
        f = self.with_field("Public gate summary", "FAIL pass 59 fail 1 no-data 0 (source=gate.txt)")
        self.assertEqual(self.check(f), [])


class Sources(Base):  # REQ-RC0-05, REQ-RC0-11
    LABEL = "Public gate summary"

    def test_no_source_path(self):
        self.assertRefused(self.check(self.with_field(self.LABEL, "PASS pass 59 fail 0 no-data 0")),
                           "no (source=<path>)")

    def test_missing_source_file(self):
        f = self.with_field(self.LABEL, "PASS pass 59 fail 0 no-data 0 (source=nope.txt)")
        self.assertRefused(self.check(f), "source unreadable")

    def test_unreadable_source(self):
        self.write_source("gate.txt", None, data=b"pass 59   fail 0   no-data 0\n\xff\xfe\n")
        self.assertRefused(self.check(), "source unreadable")

    def test_source_outside_evidence_root(self):
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w") as f:
            f.write("pass 59   fail 0   no-data 0\n")
        f = self.with_field(self.LABEL, "PASS pass 59 fail 0 no-data 0 (source=%s)" % outside)
        self.assertRefused(self.check(f), "outside the evidence root")

    def test_zero_byte_source_reads_no_data(self):
        self.write_source("host.txt", "")
        self.assertRefused(self.check(), "says PASS but its source says NO-DATA")

    def test_source_older_than_candidate_is_refused(self):
        p = os.path.join(self.ev, "host.txt")
        ct = int(git(self.root, "show", "-s", "--format=%ct", self.sha))
        os.utime(p, (ct - 3600, ct - 3600))
        self.assertRefused(self.check(), "older than the candidate commit")


class Commits(Base):  # REQ-RC0-06
    def test_made_up_sha(self):
        f = self.with_field("Candidate SHA/branch", "0123456789abcdef0123456789abcdef01234567 on build/113")
        self.assertRefused(self.check(f), "is not a commit")

    def test_blob_sha(self):
        self.assertIs(R.commit_exists(self.root, self.blob), False)
        f = self.with_field("Baseline SHA/tag", "%s (v1.1.2)" % self.blob)
        self.assertRefused(self.check(f), "is not a commit")

    def test_no_sha_at_all(self):
        self.assertRefused(self.check(self.with_field("Baseline SHA/tag", "v1.1.2")), "names no commit")

    def test_commit_exists_true(self):
        self.assertIs(R.commit_exists(self.root, self.sha), True)
        self.assertIs(R.commit_exists(self.root, self.sha[:7]), True)
        self.assertIs(R.commit_exists(self.root, "zz"), False)

    def test_git_unavailable_is_its_own_problem(self):
        with mock.patch.object(R.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(R.commit_exists(self.root, self.sha))
            self.assertRefused(self.check(), "git could not run")


class Units(Base):  # REQ-RC0-07
    LANDED = "Landed U-items and linked tests"
    REFUSED = "Reverted/refused items and reasons"

    def test_unit_not_in_wbs(self):
        self.assertRefused(self.check(self.with_field(self.LANDED, "ZZ9: python3 -B scripts/test_x.py")),
                           "landed unit ZZ9 is not in the WBS")

    def test_unit_without_test_command(self):
        self.assertRefused(self.check(self.with_field(self.LANDED, "CD1")), "landed unit CD1 has no test command")

    def test_unit_test_path_missing(self):
        self.assertRefused(self.check(self.with_field(self.LANDED, "CD1: python3 -B scripts/test_gone.py")),
                           "does not exist")

    def test_refused_without_reason(self):
        self.assertRefused(self.check(self.with_field(self.REFUSED, "U01")), "refused unit U01 has no reason")

    def test_refused_not_in_wbs(self):
        self.assertRefused(self.check(self.with_field(self.REFUSED, "ZZ9: spec wrong")),
                           "refused unit ZZ9 is not in the WBS")

    def test_none_refused_is_allowed(self):
        self.assertEqual(self.check(self.with_field(self.REFUSED, "none")), [])

    def test_sub_unit_and_many(self):
        many = "; ".join("U%02d.a: python3 -B scripts/test_x.py" % i for i in range(38))
        self.assertEqual(self.check(self.with_field(self.LANDED, many)), [])

    def test_unreadable_wbs(self):
        os.remove(os.path.join(self.root, "docs", "plan", "BROTHER-1.1.3-WBS.json"))
        self.assertRefused(self.check(), "WBS unreadable")

    def test_empty_risks(self):
        f = self.with_field("Unresolved risks that block stronger claims", "   ")
        self.assertRefused(self.check(f), "Unresolved risks")


class OwnerDecision(Base):  # REQ-RC0-08
    LABEL = "Owner decision required"

    def test_not_pending(self):
        self.assertRefused(self.check(self.with_field(self.LABEL, "accept, hold or abandon. NO AUTO RELEASE.")),
                           "begin PENDING")

    def test_missing_option(self):
        self.assertRefused(self.check(self.with_field(self.LABEL, "PENDING: accept or hold. NO AUTO RELEASE.")),
                           "abandon")

    def test_missing_no_auto_release(self):
        self.assertRefused(self.check(self.with_field(self.LABEL, "PENDING: accept, hold or abandon.")),
                           "NO AUTO RELEASE")

    def test_tagged_claim(self):
        self.assertRefused(self.check(extra="The candidate was tagged v1.1.3.\n"), "claims a tag")

    def test_public_push_claim(self):
        self.assertRefused(self.check(extra="It was pushed to the public tree.\n"), "claims a tag")

    def test_released_claim(self):
        self.assertRefused(self.check(extra="1.1.3 is Released.\n"), "claims a tag")


class Main(Base):
    def run_main(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = R.main(argv)
        return rc, buf.getvalue()

    def test_pass_and_fail_and_absent(self):
        p = os.path.join(self.tmp, "H.md")
        with open(p, "w") as f:
            f.write(self.render())
        rc, out = self.run_main(["--check", p, "--root", self.root, "--evidence", self.ev])
        self.assertEqual((rc, out.splitlines()[-1]), (0, "PASS handover"))
        self.assertIn("mtime", out)
        with open(p, "w") as f:
            f.write(self.render(self.with_field("Owner decision required", "accept")))
        rc, out = self.run_main(["--check", p, "--root", self.root, "--evidence", self.ev])
        self.assertEqual(rc, 1)
        self.assertNotIn("PASS handover", out)
        rc, out = self.run_main(["--check", os.path.join(self.tmp, "absent.md"), "--root", self.root])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out)

    def test_rewrite_is_stable(self):
        self.assertEqual(self.render(), self.render())


class Real(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(REAL_HANDOVER) and os.path.isdir(R.DEFAULT_EVIDENCE),
                         "NO-DATA: the release candidate handover is not written yet")
    def test_real_handover_passes(self):  # REQ-RC0-09
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = R.main(["--check", REAL_HANDOVER, "--root", ROOT, "--evidence", R.DEFAULT_EVIDENCE])
        self.assertEqual(rc, 0, buf.getvalue())

    @unittest.skipUnless(os.path.isfile(RUNBOOK), "NO-DATA: the runbook does not ship in this tree")
    def test_runbook_section(self):  # REQ-RC0-10
        with open(RUNBOOK, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue("\n## RC0 Release candidate and handover\n" in text, "runbook lacks the RC0 section")
        section = text.split("\n## RC0 Release candidate and handover\n", 1)[1]
        for needle in ("never passes `--yes`", "never reads an answer file", "never tags",
                       "never pushes the public tree", "python3 scripts/rc_handover.py --check",
                       "python3 scripts/cut_driver.py run --version 1.1.3", "cut_driver.py resume"):
            self.assertTrue(needle in section, "RC0 section lacks %r" % needle)


if __name__ == "__main__":
    unittest.main()
