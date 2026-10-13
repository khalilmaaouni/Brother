"""AC1: a human acceptance binds to the exact receipt it accepts.

AC1.a: accept_delivery.resolve_receipt reads a run's canonical receipt
through receipt_door.read_receipt_verdict (the one reader) and refuses, by
name, a receipt it cannot read, a receipt that belongs to another run, a run
of another project, a run at another revision than the SHA ref accepted, a
run a later run of the same outcome superseded, and a receipt already
accepted under another ref. A refusal writes nothing.

AC1.b: a review skip is human authored (three flags), stamped (skipped_at,
an ISO date), and shown on its own line in --list, never counted as a
review.

Every fixture is built under a temp folder; nothing reads HOME, nothing
starts ps. Driven through main() wherever the control lives there.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import accept_delivery as ad  # noqa: E402
import brother_run as _br  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

OUTCOME = "guard non-numeric input"
REV = "da88480d731ddfa3cb2862066d56a43e00000000"


def make_run(run_dir, project, outcome=OUTCOME, rev=REV, receipt=True,
             target=True, skip=None):
    """A completed run folder in the shape accept_delivery reads: the
    ChecksDerivedFromARunDirectory Work document and claims, a target.json
    naming `project`, and a receipt written the way
    test_brother_run_truth_render writes one (bound to its own path)."""
    os.makedirs(run_dir)
    work_skip = {} if skip is False else {"review_skip": skip}
    if skip is None:
        skip = {"reason": "covered by its own test class",
                "skipped_by": "test fixture",
                "skipped_at": "2026-10-12T09:00:00"}
        work_skip = {"review_skip": skip}
    with open(os.path.join(run_dir, "W-toy.json"), "w",
              encoding="utf-8") as fh:
        json.dump({
            "work_id": "W-toy", "outcome": outcome,
            "rows": [{"id": "guard", "title": "guard",
                      "done_check": "python3 -m pytest test_mathlib.py -q",
                      "owns": ["mathlib.py"], "depends_on": [],
                      "status": "DONE", "check_passed_before": False,
                      "files_changed_by_unit": ["mathlib.py"]}],
            **work_skip}, fh)
    with open(os.path.join(run_dir, "claims.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"guard": {"state": "done", "unit_id": "guard",
                             "evidence": {
                                 "canonical_rev": rev,
                                 "check_command":
                                     "python3 -m pytest test_mathlib.py -q",
                                 "exit_code": 0, "output": "1 passed",
                                 "output_truncated": False}}}, fh)
    if target:
        with open(os.path.join(run_dir, _br.TARGET_FILENAME), "w",
                  encoding="utf-8") as fh:
            json.dump({"cwd": os.path.abspath(project)}, fh)
    if receipt:
        write_receipt(run_dir)
    return run_dir


def receipt_path(run_dir):
    return os.path.join(run_dir, _br.RECEIPT_DIRNAME, _br.RECEIPT_FILENAME)


def write_receipt(run_dir, named=None, drop=None):
    path = receipt_path(run_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {k: "x" for k in _br.RECEIPT_REQUIRED_FIELDS}
    body.update(scope={"changed": [{"unit": "guard", "file": "mathlib.py"}]},
                evidence=[{"id": "guard", "state": "verified",
                           "command": "python3 -m pytest test_mathlib.py -q",
                           "exit_code": 0}],
                unproven=[],
                host_capability={"receipt_path": named or path})
    if drop:
        del body[drop]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    return path


def run_git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-C", cwd] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, env=env, timeout=60, check=True
    ).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ac1-binding-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = os.path.join(self.tmp, "a", "project")
        os.makedirs(self.project)
        self.runs = os.path.join(self.tmp, "runs")
        self.out = os.path.join(self.tmp, "deliveries")
        self.run = make_run(os.path.join(self.runs, "run-20261012T0900"),
                            self.project)

    def resolve(self, ref="toy-1", run=None, project=None):
        return ad.resolve_receipt(run or self.run, project or self.project,
                                  ref, self.out)

    def refused(self, kind, **kw):
        binding, reason = self.resolve(**kw)
        self.assertIsNone(binding, reason)
        self.assertTrue(reason.startswith(kind + ":"), reason)
        return reason

    def accept(self, ref="toy-1", run=None, project=None, extra=()):
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = ad.main([
                "--name", "toy delivery", "--ref", ref,
                "--accepted-by", "Khalil Maaouni",
                "--accepted-at", "2026-10-12", "--recorded-by", "person",
                "--run-dir", run or self.run,
                "--project-cwd", project or self.project,
                "--dir", self.out,
                "--pattern-root", os.path.join(self.tmp, "vault")]
                + list(extra))
        return code, out.getvalue() + err.getvalue()

    def listing(self):
        return sorted(os.listdir(self.out)) if os.path.isdir(self.out) else []


class Unreadable(Base):
    """REQ-AC1-01."""

    def test_no_receipt_folder_is_refused(self):
        shutil.rmtree(os.path.join(self.run, _br.RECEIPT_DIRNAME))
        self.refused("unreadable")

    def test_a_zero_byte_receipt_is_refused(self):
        open(receipt_path(self.run), "w").close()
        self.refused("unreadable")

    def test_a_field_short_receipt_is_refused(self):
        write_receipt(self.run, drop="host_capability")
        self.refused("unreadable")
        write_receipt(self.run, drop=_br.RECEIPT_REQUIRED_FIELDS[0])
        self.refused("unreadable")


class OtherRun(Base):
    """REQ-AC1-02."""

    def setUp(self):
        super().setUp()
        self.other = make_run(os.path.join(self.tmp, "elsewhere", "run-x"),
                              self.project)

    def test_a_copied_receipt_is_refused(self):
        shutil.copyfile(receipt_path(self.other), receipt_path(self.run))
        reason = self.refused("other-run")
        self.assertIn(receipt_path(self.other), reason)

    def test_a_symlinked_receipt_is_refused(self):
        os.remove(receipt_path(self.run))
        os.symlink(receipt_path(self.other), receipt_path(self.run))
        self.refused("other-run")


class ForeignProject(Base):
    """REQ-AC1-03."""

    def test_a_run_of_another_project_with_the_same_basename_is_refused(self):
        twin = os.path.join(self.tmp, "b", "project")
        os.makedirs(twin)
        reason = self.refused("foreign-project", project=twin)
        self.assertIn(twin, reason)
        self.assertIn(self.project, reason)

    def test_a_run_with_no_target_is_refused(self):
        os.remove(os.path.join(self.run, _br.TARGET_FILENAME))
        self.refused("foreign-project")

    def test_a_target_that_is_an_array_is_refused(self):
        with open(os.path.join(self.run, _br.TARGET_FILENAME), "w") as fh:
            json.dump([self.project], fh)
        self.refused("foreign-project")

    def test_main_refuses_a_run_of_another_project_and_writes_nothing(self):
        twin = os.path.join(self.tmp, "b", "project")
        os.makedirs(twin)
        code, text = self.accept(project=twin)
        self.assertEqual(code, 2, text)
        self.assertIn("refused: foreign-project:", text)
        self.assertEqual(self.listing(), [])


class OldRevision(Base):
    """REQ-AC1-04."""

    def setUp(self):
        super().setUp()
        run_git(self.project, "init", "-q")
        run_git(self.project, "commit", "-q", "--allow-empty", "-m", "one")
        self.first = run_git(self.project, "rev-parse", "HEAD")
        run_git(self.project, "commit", "-q", "--allow-empty", "-m", "two")
        self.second = run_git(self.project, "rev-parse", "HEAD")

    def _run_at(self, rev):
        shutil.rmtree(self.run)
        make_run(self.run, self.project, rev=rev)

    def test_a_sha_ref_equal_to_the_delivered_revision_is_accepted(self):
        self._run_at(self.second)
        binding, reason = self.resolve(ref=self.second[:12])
        self.assertEqual(reason, "")
        self.assertEqual(binding["revision_check"], "PASS")

    def test_a_sha_ref_that_is_not_the_delivered_revision_is_refused(self):
        self._run_at(self.first)
        reason = self.refused("old-revision", ref=self.second)
        self.assertIn(self.first, reason)
        self.assertIn(self.second, reason)

    def test_a_pr_style_ref_records_no_data(self):
        self._run_at(self.first)
        binding, reason = self.resolve(ref="owner/repo#5")
        self.assertEqual(reason, "")
        self.assertEqual(binding["revision_check"], ad.NODATA)


class Superseded(Base):
    """REQ-AC1-05."""

    def test_an_older_run_of_the_same_outcome_is_refused(self):
        newer = make_run(os.path.join(self.runs, "run-20261012T1000"),
                         self.project, outcome="  Guard NON-numeric input ")
        reason = self.refused("superseded")
        self.assertIn("run-20261012T1000", reason)
        binding, reason = self.resolve(run=newer)
        self.assertEqual(reason, "", reason)

    def test_a_later_run_of_another_outcome_or_project_does_not_supersede(self):
        make_run(os.path.join(self.runs, "run-20261012T1000"), self.project,
                 outcome="something else")
        make_run(os.path.join(self.runs, "run-20261012T1100"),
                 os.path.join(self.tmp, "b", "project"))
        make_run(os.path.join(self.runs, "run-20261012T1200"), self.project,
                 receipt=False)
        for i in range(30):
            make_run(os.path.join(self.runs, "run-20261011T%04d" % i),
                     self.project)
        binding, reason = self.resolve()
        self.assertEqual(reason, "", reason)


class Duplicate(Base):
    """REQ-AC1-06."""

    def test_the_same_receipt_under_a_second_ref_is_refused(self):
        code, text = self.accept(ref="toy-1")
        self.assertEqual(code, 0, text)
        held, bad = ad.scan_receipts(self.out)
        self.assertEqual(bad, [])
        self.assertEqual(list(held.values()), ["toy-1"])
        reason = self.refused("duplicate", ref="toy-2")
        self.assertIn("toy-1", reason)
        before = self.listing()
        code, text = self.accept(ref="toy-2")
        self.assertEqual(code, 2, text)
        self.assertIn("refused: duplicate:", text)
        self.assertEqual(self.listing(), before)

    def test_a_corrupt_delivery_record_refuses(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "broken.json"), "wb") as fh:
            fh.write(b'{"ref": "x\x00", "run": {}}')
        held, bad = ad.scan_receipts(self.out)
        self.assertEqual(bad, ["broken.json"])
        reason = self.refused("duplicate")
        self.assertIn("broken.json", reason)


class CleanAcceptance(Base):
    """REQ-AC1-07."""

    def test_a_clean_run_is_recorded_with_its_receipt_binding(self):
        code, text = self.accept(ref="toy-1")
        self.assertEqual(code, 0, text)
        with open(receipt_path(self.run), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        with open(ad.record_path("toy-1", self.out), encoding="utf-8") as fh:
            run = json.load(fh)["run"]
        self.assertEqual(run["receipt_sha256"], digest)
        self.assertEqual(run["receipt_state"], "DONE")
        self.assertEqual(run["receipt_exit_code"], 0)
        self.assertEqual(run["revision_check"], ad.NODATA)
        self.assertEqual(run["run_id"], os.path.basename(self.run))

    def test_the_same_refused_command_refuses_again_the_same_way(self):
        os.remove(os.path.join(self.run, _br.TARGET_FILENAME))
        first = self.accept()
        second = self.accept()
        self.assertEqual(first, second)
        self.assertEqual(first[0], 2)


class RefusalWritesNothing(Base):
    """REQ-AC1-08: each refusal kind, through main(), leaves the deliveries
    folder exactly as it was."""

    def test_no_refusal_writes_a_record(self):
        other = make_run(os.path.join(self.tmp, "elsewhere", "run-x"),
                         self.project)
        twin = os.path.join(self.tmp, "b", "project")
        os.makedirs(twin)
        cases = []

        def unreadable():
            os.remove(receipt_path(self.run))
        cases.append(("unreadable", unreadable, {}))

        def other_run():
            shutil.copyfile(receipt_path(other), receipt_path(self.run))
        cases.append(("other-run", other_run, {}))
        cases.append(("foreign-project", lambda: None, {"project": twin}))

        def superseded():
            make_run(os.path.join(self.runs, "run-20261012T1000"),
                     self.project)
        cases.append(("superseded", superseded, {}))
        for kind, arrange, kw in cases:
            with self.subTest(kind=kind):
                shutil.rmtree(self.runs, ignore_errors=True)
                make_run(self.run, self.project)
                arrange()
                before = self.listing()
                code, text = self.accept(ref="toy-%s" % kind, **kw)
                self.assertEqual(code, 2, text)
                self.assertIn("refused: %s:" % kind, text)
                self.assertEqual(self.listing(), before)


class HumanSkip(Base):
    """AC1.b, REQ-AC1-09 to REQ-AC1-11: a skip is human authored, names who,
    why and when, and is never inferred."""

    SKIP = ["--review-skip-reason", "docs only, no risk boundary crossed",
            "--review-skip-by", "Khalil Maaouni",
            "--review-skip-at", "2026-10-12T10:00:00"]

    def test_a_review_skip_with_no_timestamp_is_refused(self):
        shutil.rmtree(self.run)
        make_run(self.run, self.project,
                 skip={"reason": "docs only", "skipped_by": "Khalil Maaouni"})
        review, reason = ad.review_from_run_dir(self.run)
        self.assertIsNone(review)
        self.assertIn("review_skip names no skipped_at", reason)
        code, text = self.accept()
        self.assertEqual(code, 2, text)
        self.assertEqual(self.listing(), [])

    def test_human_skip_needs_identity_reason_and_an_iso_timestamp(self):
        entry, why = ad.human_skip("docs only", "Khalil Maaouni",
                                   "2026-10-12T10:00:00")
        self.assertEqual(why, "")
        self.assertEqual(entry, {"skipped": True, "reason": "docs only",
                                 "skipped_by": "Khalil Maaouni",
                                 "skipped_at": "2026-10-12T10:00:00"})
        for args, word in ((("  ", "K", "2026-10-12"), "reason"),
                           (("r", "", "2026-10-12"), "skipped_by"),
                           (("r", "K", ""), "skipped_at"),
                           (("r", "K", "yesterday"), "yesterday")):
            with self.subTest(args=args):
                entry, why = ad.human_skip(*args)
                self.assertEqual(entry, {})
                self.assertIn(word, why)

    def test_a_cited_run_with_no_review_and_no_skip_is_refused(self):
        shutil.rmtree(self.run)
        make_run(self.run, self.project, skip=False)
        code, text = self.accept()
        self.assertEqual(code, 2, text)
        self.assertIn("review_skip", text)
        self.assertEqual(self.listing(), [])

    def test_the_skip_flags_record_a_human_skip(self):
        shutil.rmtree(self.run)
        make_run(self.run, self.project, skip=False)
        code, text = self.accept(extra=self.SKIP)
        self.assertEqual(code, 0, text)
        with open(ad.record_path("toy-1", self.out), encoding="utf-8") as fh:
            review = json.load(fh)["review"]
        self.assertEqual(review["skipped_at"], "2026-10-12T10:00:00")
        self.assertEqual(review["skipped_by"], "Khalil Maaouni")

    def test_empty_skip_flags_are_refused(self):
        code, text = self.accept(extra=["--review-skip-reason", "",
                                        "--review-skip-by", "",
                                        "--review-skip-at", ""])
        self.assertEqual(code, 2, text)
        self.assertEqual(self.listing(), [])

    def test_the_skip_flags_are_all_or_none(self):
        for i in range(3):
            partial = self.SKIP[:2 * i] + self.SKIP[2 * i + 2:]
            with self.subTest(dropped=self.SKIP[2 * i]):
                with self.assertRaises(SystemExit) as ctx:
                    self.accept(extra=partial)
                self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.listing(), [])

    def test_the_skip_flags_need_a_run_dir(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            ad.main(["--name", "n", "--ref", "r", "--accepted-by", "K",
                     "--accepted-at", "2026-10-12", "--recorded-by",
                     "person", "--dir", self.out] + self.SKIP)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--run-dir", err.getvalue())
        self.assertEqual(self.listing(), [])


class ListingShowsTheReview(Base):
    """AC1.b, REQ-AC1-12 and REQ-AC1-13."""

    def write(self, name, **fields):
        os.makedirs(self.out, exist_ok=True)
        entry = {"name": name, "ref": name, "accepted_by": "K",
                 "accepted_at": "2026-10-12", "recorded_by": "person"}
        entry.update(fields)
        path = os.path.join(self.out, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
        return path

    def list_out(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ad.main(["--list", "--dir", self.out])
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_a_skip_lists_as_skipped_and_is_counted_apart(self):
        self.write("reviewed", review={"reviewer": "backend-reviewer",
                                       "reviewed_revision": "abc1234"})
        self.write("skipped", recorded_by="agent", delegation="do it",
                   review={"skipped": True, "reason": "docs only",
                           "skipped_by": "K",
                           "skipped_at": "2026-10-12T10:00:00"})
        text = self.list_out()
        self.assertIn("review: backend-reviewer at abc1234", text)
        self.assertIn("review: SKIPPED by K at 2026-10-12T10:00:00 because "
                      "docs only", text)
        self.assertIn("reviewed: 1, skipped: 1, no-data: 0", text)

    def test_twenty_records_add_up(self):
        for i in range(20):
            review = ({"reviewer": "r", "reviewed_revision": "x"} if i % 3 == 0
                      else {"skipped": True, "reason": "r", "skipped_by": "K",
                            "skipped_at": "2026-10-12"} if i % 3 == 1
                      else None)
            self.write("rec-%02d" % i, **({"review": review} if review
                                          else {}))
        self.assertIn("reviewed: 7, skipped: 7, no-data: 6", self.list_out())

    def test_a_legacy_record_lists_as_no_data_and_is_not_rewritten(self):
        path = self.write("legacy")
        corrupt = self.write("corrupt", review="looked fine")
        before = [open(p, "rb").read() for p in (path, corrupt)]
        text = self.list_out()
        self.assertIn("review: NO-DATA (record predates the field)", text)
        self.assertIn("reviewed: 0, skipped: 0, no-data: 2", text)
        self.assertEqual([open(p, "rb").read() for p in (path, corrupt)],
                         before)


if __name__ == "__main__":
    unittest.main()
