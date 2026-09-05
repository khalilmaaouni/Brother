"""test_capsule_items: row S13, the fifteen zone-3 items on the capsule.

docs/plan/SWITCHING-STRATEGY-2026-09-04.md "Zone 3 : Engineering Continuity"
names fifteen things a resume should recover. scripts/continuity.py's own
capsule() answered four of them (objective, canonical_revision, environment,
next_action) before this row; it now also builds cap["zone3"], a dict
carrying all fifteen under the document's own names (continuity.ZONE3_ITEMS),
each one a real value or an explicit NO-DATA sentence, never an empty string
and never a missing key. This file drives that both ways: a run built and
then killed (mirroring test_crash_resume.py's own
ACapsuleSurvivesAKillAfterIntegration -- claim_store and journal called
directly against a bare run_dir, no subprocess) must print and hold all
fifteen; a copy of the capsule with one key deleted must fail the same check
by naming the missing key.

The temp git repo fixture mirrors test_brother_run.py's own
_git_repo_with_file: a real committed checkout, so the git-backed items
(CANONICAL COMMIT, CHANGED FILES, ACTIVE / FINISHED WORKTREES) resolve to
real answers rather than NO-DATA by construction, the same positive-control
reasoning test_continuity.py's EnvironmentAssumptions class already uses for
the fields it drives.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402


def _sh(args, cwd=None):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=30)


def _git_repo_with_file(tmp, name, body="content\n"):
    """A tiny real git repo with one committed file, mirroring
    test_brother_run.py's own _git_repo_with_file, so this fixture resolves
    real canonical revisions and a real clean working tree rather than
    NO-DATA."""
    repo = os.path.join(tmp, "canon")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        _sh(args, cwd=repo)
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(body)
    _sh(["add", "-A"], cwd=repo)
    _sh(["commit", "-q", "-m", "R0"], cwd=repo)
    rev = _sh(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    return repo, rev


def _check_zone3_complete(zone3):
    """(ok, problem): every one of continuity.ZONE3_ITEMS is a key in
    `zone3` and its value is truthy (a real value or a NO-DATA sentence,
    never absent, never an empty string). Extracted so this file's own
    missing-key test can drive it against a deliberately broken copy,
    proving the check both ways rather than only on the good case."""
    missing = [name for name in continuity.ZONE3_ITEMS if name not in zone3]
    if missing:
        return False, "missing zone3 key(s): %s" % ", ".join(missing)
    empty = [name for name in continuity.ZONE3_ITEMS
             if zone3[name] == "" or zone3[name] is None]
    if empty:
        return False, "empty zone3 value(s): %s" % ", ".join(empty)
    return True, ""


class AllFifteenZoneThreeItemsSurviveAKill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="capsule-items-")
        self.repo, self.rev = _git_repo_with_file(self.tmp, "a.txt")
        self.run_dir = tempfile.mkdtemp(prefix="capsule-items-run-")
        claims_path = os.path.join(self.run_dir, "claims.json")
        claim, problem = claim_store.acquire(claims_path, "U1", "workerA")
        self.assertTrue(claim, problem)
        claim_store.release(claims_path, "U1", "workerA", state="done",
                            evidence={"exit_code": 0})
        with open(os.path.join(self.run_dir, "W-w1.json"), "w",
                 encoding="utf-8") as fh:
            json.dump({"outcome": "prove the fifteen zone-3 items",
                      "work_id": "w1",
                      "rows": [{"id": "U1", "title": "first",
                               "status": "DONE", "done_check": "true"},
                              {"id": "U2", "title": "second",
                               "status": "SCHEDULED", "done_check": "true"}]},
                     fh)
        journal.append(self.run_dir, "run.opened",
                       payload={"cwd": self.repo, "resumed": False})
        journal.append(self.run_dir, "dispatch.round",
                       payload={"slots": 2, "own_tools": True})
        journal.append(self.run_dir, "unit.done", unit_id="U1", payload={})
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        # THE KILL: nothing more is ever written to run_dir after this
        # point, exactly what a SIGKILL leaves behind (test_crash_resume.py's
        # own comment on the same shape).

    def test_the_capsule_printed_after_the_kill_names_all_fifteen_items(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = continuity.main([self.run_dir])
        self.assertEqual(code, 0)
        printed = out.getvalue()
        for name in continuity.ZONE3_ITEMS:
            self.assertIn(name, printed)

    def test_every_item_is_a_real_value_or_an_explicit_no_data(self):
        cap, problem = continuity.capsule(self.run_dir)
        self.assertEqual(problem, "")
        ok, why = _check_zone3_complete(cap["zone3"])
        self.assertTrue(ok, why)
        # The git-backed items resolve to real answers, never NO-DATA,
        # because this fixture is a real committed repo with a clean tree.
        self.assertEqual(cap["zone3"]["CANONICAL COMMIT"], self.rev)
        self.assertIn("clean", cap["zone3"]["CHANGED FILES"])

    def test_removing_one_key_from_a_copy_fails_naming_it(self):
        """Driven both ways: the check above must fail, and name the gap,
        when a zone3 key goes missing."""
        cap, problem = continuity.capsule(self.run_dir)
        self.assertEqual(problem, "")
        broken = dict(cap["zone3"])
        del broken["RELEVANT LESSONS"]
        ok, why = _check_zone3_complete(broken)
        self.assertFalse(ok)
        self.assertIn("RELEVANT LESSONS", why)


if __name__ == "__main__":
    unittest.main()
