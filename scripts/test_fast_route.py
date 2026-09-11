"""FAST-0: the deterministic light-path predicate, driven both ways.

WHAT THIS PROVES, and what it deliberately leaves to other suites:
  * fast_route_eligibility() (scripts/brother_run.py) only turns outcome
    text into a candidate unit (which existing files, which existing
    check); the safety JUDGMENT is entirely fast_path.eligible()'s, one
    call, no second predicate -- so most of the adversarial cases below
    call fast_route_eligibility() directly and never touch brother_run.py's
    public entry point at all, even though fast_path.eligible() itself
    runs a subprocess or two (git status, the done_check probe) to answer.
  * The two places that DO need a real end-to-end run (how many model
    sessions actually opened, and what the existing scope audit does to an
    undeclared write) reuse scripts/tiny_task_cost.py's own instrumented
    fixtures and stub_env as the oracle, rather than re-measuring sessions
    a second, different way.
  * scripts/test_fast_path.py drives scripts/fast_path.py directly, unit
    by unit; this suite drives the SAME module through
    brother_run.fast_route_eligibility(), which now does nothing but build
    a candidate unit from outcome text and hand it to fast_path.eligible()
    (CONSOLIDATED, night run 2026-09-09: the two predicates that once
    disagreed here are one).

Standard library plus this repository's own stub seam, no network.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import product_acceptance as pa  # noqa: E402
import test_brother_run as tbr  # noqa: E402
import tiny_task_cost as ttc  # noqa: E402

#: Every subprocess-driving test below roots its own temp trees here
#: explicitly, NEVER through the process-wide tempfile.tempdir default:
#: importing test_brother_run (above) already redirected that default to a
#: tmp_sandbox root whose own name can carry a literal space (CPython's
#: `python -m unittest` rewrites sys.argv[0] to "<python> -m unittest",
#: which tmp_sandbox.install() folds into its prefix), and a path with a
#: space breaks the DOOR_MODEL_CMD/MODEL_WORKER_CMD strings this suite
#: builds and then shlex.splits downstream. /tmp itself is never touched by
#: that redirect (tmp_sandbox only ever changes tempfile.tempdir, never
#: what "/tmp" itself resolves to), so a fixture rooted here is unaffected
#: by whichever module happened to import first.
CLEAN_TMP_ROOT = "/tmp"


def _clean_tmp(prefix):
    return tempfile.mkdtemp(prefix=prefix, dir=CLEAN_TMP_ROOT)


#: TEST HARDENING (night run 2026-09-09): THE FINDING. Under machine load
#: test_eligible_code_fixture_routes_fast_one_session failed 2 of 4 isolated
#: runs (3 of 3 passing at load 3.3 for a different builder), because
#: brother_run.py's own bounded worker retry (MAX_UNIT_ATTEMPTS,
#: WORKER_TIME_LIMIT_SECONDS) reruns MODEL_WORKER_CMD after a timeout, so
#: worker_sessions can honestly read 2 on a run that only ever declared one
#: unit. That is the drain doing exactly what its own docstring promises
#: (T2: "a unit that ... NEEDS-REPAIR-ON-NEW-BASE stays SCHEDULED and may
#: be claimed again"), never a defect in the fast route: FAST-0's own claim
#: is that the PLANNER session is skipped, not that the worker never
#: retries. The two helpers below stop treating a strict "== 1" as the
#: contract and instead prove the weaker, TRUE one: at least one session,
#: and if more than one, the run's own record -- never a guess -- names the
#: retry, so it is never silent. door.py's own decomposer retry (its
#: --max-retries loop, ask_decomposer) is the planner-side twin of the same
#: shape, checked the same way against run.log's own words.
def _assert_worker_retry_is_recorded(testcase, result):
    worker_sessions = result["worker_sessions"]
    if worker_sessions <= 1:
        return
    retry = result.get("retry_record") or {}
    states = retry.get("attempt_states") or []
    testcase.assertEqual(
        len(states), worker_sessions,
        "worker_sessions is %d but the run's own attempt trace (T2, "
        "brother_run.ATTEMPTS_DIRNAME) names %d attempt(s) for unit %s "
        "(states %s): a retry must never be silent"
        % (worker_sessions, len(states), retry.get("unit_id", ttc.NODATA),
           states))
    testcase.assertIsNotNone(
        retry.get("claim_attempt"),
        "worker_sessions is %d but claims.json holds no attempt counter "
        "for unit %s" % (worker_sessions, retry.get("unit_id", ttc.NODATA)))
    testcase.assertGreaterEqual(
        retry.get("claim_attempt"), worker_sessions,
        "the claim store's own attempt counter (%r) undercounts the "
        "worker sessions actually opened (%d)"
        % (retry.get("claim_attempt"), worker_sessions))


#: door.py's own words for a decomposer retry (ask_decomposer's loop,
#: printed once per attempt, verbatim), mirrored into run.log by
#: brother_run.run_door's log.note(door_text) -- read here rather than
#: respelled, matching this file's own rule (module docstring) that a
#: fixture proves the real engine's behavior, not a private guess at it.
DECOMPOSER_ATTEMPT_MARK = "door: asking the decomposer (attempt"


def _assert_planner_retry_is_recorded(testcase, runs_root, planner_sessions):
    if planner_sessions <= 1:
        return
    log_path = ttc._run_log(runs_root)
    testcase.assertIsNotNone(
        log_path, "planner_sessions is %d but no run.log was written to "
        "explain it" % planner_sessions)
    with open(log_path, encoding="utf-8") as fh:
        log_text = fh.read()
    attempts_logged = log_text.count(DECOMPOSER_ATTEMPT_MARK)
    testcase.assertGreaterEqual(
        attempts_logged, planner_sessions,
        "planner_sessions is %d but run.log only names %d decomposer "
        "attempt(s) (%r): a retry must never be silent"
        % (planner_sessions, attempts_logged, DECOMPOSER_ATTEMPT_MARK))


class ThePredicateDecidesFromTextAlone(unittest.TestCase):
    """Rules 1-4 of the ELIGIBILITY PREDICATE, called directly: no
    subprocess, no model, no run started -- exactly what "no model call,
    evaluated once before any session opens" means."""

    def _repo(self, files):
        root = _clean_tmp("fast-route-fixture-")
        for rel, content in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        # fast_path.eligible() proves a clean git base (integrate.dirty_paths)
        # before it ever reaches the questions this class's fixtures probe,
        # so every one of them needs to already be a real, clean repo.
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root,
                       check=True)
        return root

    def test_manifest_path_stays_normal(self):
        repo = self._repo({"requirements.txt": "flask\n",
                           "scripts/test_x.py": "import unittest\n"})
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update requirements.txt, proven by scripts/test_x.py", repo)
        self.assertFalse(eligible, reason)
        self.assertIn("FAST_FORBIDDEN_PATHS", reason)

    def test_no_check_named_stays_normal(self):
        repo = self._repo({"NOTES.md": "notes\n"})
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update NOTES.md so it reads better", repo)
        self.assertFalse(eligible, reason)
        self.assertIn("no check", reason)

    def test_four_files_stays_normal(self):
        repo = self._repo({
            "a.txt": "a\n", "b.txt": "b\n", "c.txt": "c\n", "d.txt": "d\n",
            "scripts/test_x.py": "import unittest\n"})
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update a.txt b.txt c.txt d.txt, proven by scripts/test_x.py",
            repo)
        self.assertFalse(eligible, reason)
        # fast_path.eligible's own write-scope cap (2) is narrower than
        # and supersedes the old inline predicate's (3); 4 files trips
        # either cap, refused either way.
        self.assertIn("declared write paths", reason)

    def test_absolute_path_stays_normal(self):
        repo = self._repo({"scripts/test_x.py": "import unittest\n"})
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update /etc/passwd, proven by scripts/test_x.py", repo)
        self.assertFalse(eligible, reason)
        self.assertIn("escaping", reason)

    def test_dotdot_escape_stays_normal(self):
        repo = self._repo({"scripts/test_x.py": "import unittest\n"})
        # the escaping target must exist for the candidate builder (pure
        # existence detection, no safety judgment of its own) to find it
        # at all; the ESCAPE is what work_record.check_units then refuses,
        # by the path's own string form, regardless of what is there.
        with open(os.path.join(os.path.dirname(repo), "outside.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("outside\n")
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update ../outside.txt, proven by scripts/test_x.py", repo)
        self.assertFalse(eligible, reason)
        self.assertIn("escaping", reason)

    def test_symlink_stays_normal(self):
        repo = self._repo({"real.txt": "real\n",
                           "scripts/test_x.py": "import unittest\n"})
        os.symlink(os.path.join(repo, "real.txt"),
                  os.path.join(repo, "link.txt"))
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update link.txt, proven by scripts/test_x.py", repo)
        self.assertFalse(eligible, reason)
        self.assertIn("symlink", reason)

    def test_deny_term_zero_width_evasion_stays_normal(self):
        repo = self._repo({"NOTES.md": "notes\n",
                           "scripts/test_x.py": "import unittest\n"})
        outcome = ("update NOTES.md, proven by scripts/test_x.py, for the "
                  "au​th flow")
        eligible, reason, files, check = _br.fast_route_eligibility(
            outcome, repo)
        self.assertFalse(eligible, reason)

    def test_deny_term_mixed_case_evasion_stays_normal(self):
        repo = self._repo({"NOTES.md": "notes\n",
                           "scripts/test_x.py": "import unittest\n"})
        outcome = ("update NOTES.md, proven by scripts/test_x.py, for the "
                  "AuTh flow")
        eligible, reason, files, check = _br.fast_route_eligibility(
            outcome, repo)
        self.assertFalse(eligible, reason)

    def test_deny_term_hyphenated_evasion_stays_normal(self):
        repo = self._repo({"NOTES.md": "notes\n",
                           "scripts/test_x.py": "import unittest\n"})
        outcome = ("update NOTES.md, proven by scripts/test_x.py, touching "
                  "a back-fill job")
        eligible, reason, files, check = _br.fast_route_eligibility(
            outcome, repo)
        self.assertFalse(eligible, reason)

    def test_deny_term_inside_code_fence_stays_normal(self):
        repo = self._repo({"NOTES.md": "notes\n",
                           "scripts/test_x.py": "import unittest\n"})
        outcome = ("update NOTES.md, proven by scripts/test_x.py, see "
                  "```auth``` for context")
        eligible, reason, files, check = _br.fast_route_eligibility(
            outcome, repo)
        self.assertFalse(eligible, reason)

    def test_forced_exception_stays_normal_with_reason_recorded(self):
        """Predicate rule 4: any exception anywhere inside the predicate is
        caught, never propagated, and the reason names it."""
        repo = self._repo({"scripts/test_x.py": "import unittest\n"})
        with mock.patch.object(_br, "_fast_route_tokens",
                               side_effect=RuntimeError("boom")):
            eligible, reason, files, check = _br.fast_route_eligibility(
                "update NOTES.md, proven by scripts/test_x.py", repo)
        self.assertFalse(eligible)
        self.assertEqual(files, [])
        self.assertIsNone(check)
        self.assertIn("RuntimeError", reason)
        self.assertIn("boom", reason)

    def test_eligible_reason_names_the_count_and_no_risk_term(self):
        """The one PASS case at this level, so the FAIL cases above are read
        against a working control, not just against each other."""
        repo = self._repo({"NOTES.md": "notes\n",
                           "scripts/test_x.py": "import unittest\n"})
        eligible, reason, files, check = _br.fast_route_eligibility(
            "update NOTES.md, proven by scripts/test_x.py", repo)
        self.assertTrue(eligible, reason)
        self.assertEqual(files, ["NOTES.md"])
        self.assertEqual(check, "python3 -m unittest scripts.test_x")
        # the eligibility judgment itself is now fast_path.eligible's, so
        # the reason is that module's own words, not the old inline
        # predicate's.
        self.assertEqual(
            reason, "every FAST-0 condition (steering 8.3) proven true")


class TheFastRouteEndToEnd(unittest.TestCase):
    """ONE session (the worker), never two, for a real end-to-end run
    through the public entry point. Reuses tiny_task_cost's own
    instrumented fixtures as the oracle: THE INSTRUMENT and this suite must
    never disagree about what a session count means."""

    def test_eligible_docs_fixture_routes_fast_one_session(self):
        """FAST-0's claim: no planner session, ever. The worker itself is
        allowed at least one retry under load (TEST HARDENING, night run
        2026-09-09, see _assert_worker_retry_is_recorded above) as long as
        the run's own record explains it."""
        tmp = _clean_tmp("fast-route-docs-")
        result = ttc.case_docs_eligible(tmp)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["planner_sessions"], 0, result)
        self.assertGreaterEqual(result["worker_sessions"], 1, result)
        _assert_worker_retry_is_recorded(self, result)

    def test_eligible_code_fixture_routes_fast_one_session(self):
        """Same contract as the docs fixture above, and the exact fixture
        the finding was measured against: 2 of 4 isolated runs on a loaded
        machine opened a second worker session, a bounded retry the drain
        is entitled to take (T2, MAX_UNIT_ATTEMPTS)."""
        tmp = _clean_tmp("fast-route-code-")
        result = ttc.case_code_eligible(tmp)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["planner_sessions"], 0, result)
        self.assertGreaterEqual(result["worker_sessions"], 1, result)
        _assert_worker_retry_is_recorded(self, result)

    def test_auth_worded_outcome_stays_normal_two_sessions(self):
        """An outcome that would otherwise be eligible (one existing file,
        one existing check) but also carries a risk word: the normal,
        decomposed route, and its true price is one planner plus one
        worker session."""
        tmp = _clean_tmp("fast-route-auth-")
        repo = tbr.make_repo(tmp)
        scripts_dir = os.path.join(repo, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        with open(os.path.join(repo, "NOTES.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("notes\n")
        with open(os.path.join(scripts_dir, "test_notes.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(ttc.SEEDED_NOTES_TEST)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo,
                       check=True)

        decomposer_body = """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "update the auth notes",
                 "done_check": "python3 -m unittest scripts.test_notes",
                 "writes": ["NOTES.md"], "deps": []},
            ]))
        """
        env = ttc.stub_env(tmp, decomposer_body, tbr.WRITER_MODEL)
        runs_root = os.path.join(tmp, "runs")
        os.makedirs(runs_root, exist_ok=True)
        outcome = ("update NOTES.md, proven by scripts/test_notes.py, for "
                  "the auth flow")
        proc = subprocess.run(
            [sys.executable, ttc.BROTHER_RUN, outcome, "--cwd", repo,
             "--runs-root", runs_root], cwd=repo, env=env,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        planner, worker = ttc._read_sessions(tmp)
        # TEST HARDENING (night run 2026-09-09): the same weaker, true
        # contract as the fast-route fixtures above -- at least one
        # decomposer session, and if door.py retried it (its own
        # --max-retries loop) that retry is named in run.log, never
        # silent.
        self.assertGreaterEqual(
            planner, 1, "an auth-worded outcome must still be decomposed: "
            "the deny term made it ineligible")
        _assert_planner_retry_is_recorded(self, runs_root, planner)
        self.assertEqual(worker, 1)


#: The scope-audit fixture shared by both routes below: a decomposer (or a
#: fast-built plan) declares a.txt, and the worker writes b.txt instead --
#: the exact undeclared-write shape docs/plan/runs already quarantine, its
#: own wording measured live and reused here rather than guessed.
UNDECLARED_MODEL = """
    with open("b.txt", "w") as fh:
        fh.write("undeclared write\\n")
    print("stub model wrote b.txt instead of a.txt")
"""


class AnUndeclaredWriteEscalatesEitherWay(unittest.TestCase):
    """The contract's own words: "the existing scope audit, escalation and
    quarantine apply unchanged when the worker touches an undeclared file".
    Both routes below hit the SAME quarantine wording from integrate.py,
    because neither one ever reaches a different code path for it."""

    def test_normal_route_undeclared_write_is_quarantined(self):
        tmp = _clean_tmp("fast-route-undeclared-normal-")
        repo = tbr.make_repo(tmp)
        with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("seed\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed a.txt"], cwd=repo,
                       check=True)
        decomposer_body = """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "edit a.txt",
                 "done_check": "test -f b.txt", "writes": ["a.txt"],
                 "deps": []},
            ]))
        """
        env = ttc.stub_env(tmp, decomposer_body, UNDECLARED_MODEL)
        runs_root = os.path.join(tmp, "runs")
        os.makedirs(runs_root, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, ttc.BROTHER_RUN, "declare a.txt but write b.txt",
             "--cwd", repo, "--runs-root", runs_root], cwd=repo, env=env,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("QUARANTINE", proc.stdout)
        self.assertIn("never declared: b.txt", proc.stdout)
        planner, worker = ttc._read_sessions(tmp)
        # TEST HARDENING (night run 2026-09-09): the same weaker, true
        # contract as above -- see _assert_planner_retry_is_recorded.
        self.assertGreaterEqual(planner, 1)
        _assert_planner_retry_is_recorded(self, runs_root, planner)

    def test_fast_route_undeclared_write_is_quarantined_the_same_way(self):
        tmp = _clean_tmp("fast-route-undeclared-fast-")
        repo = tbr.make_repo(tmp)
        scripts_dir = os.path.join(repo, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("seed\n")
        with open(os.path.join(scripts_dir, "test_always.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("import os\nimport unittest\n\n\n"
                     "class Always(unittest.TestCase):\n"
                     "    def test_true(self):\n"
                     "        self.assertTrue(os.path.isfile(\"proof.txt\"))\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo,
                       check=True)
        env = ttc.stub_env(tmp, ttc.POISON_DECOMPOSER, UNDECLARED_MODEL)
        runs_root = os.path.join(tmp, "runs")
        os.makedirs(runs_root, exist_ok=True)
        outcome = "edit a.txt and prove it with scripts/test_always.py"
        proc = subprocess.run(
            [sys.executable, ttc.BROTHER_RUN, outcome, "--cwd", repo,
             "--runs-root", runs_root], cwd=repo, env=env,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("QUARANTINE", proc.stdout)
        self.assertIn("never declared: b.txt", proc.stdout)
        planner, worker = ttc._read_sessions(tmp)
        self.assertEqual(planner, 0, "the fast route must never call the "
                         "decomposer, even on a run that ends refused")


class TheReceiptShapeMatches(unittest.TestCase):
    """FAST-0 changes nothing about receipt_door.py: per_file_checks() reads
    only receipts and rows, never how the plan was built, so its entries
    carry the identical key set whichever route produced them."""

    @staticmethod
    def _receipt_entry(result, tmp, case_name):
        runs_root = os.path.join(tmp, "runs-" + case_name)
        receipt_rel = next(
            p for p in result["files_written_in_runs_root"]
            if p.endswith("receipt/receipt.json"))
        with open(os.path.join(runs_root, receipt_rel),
                  encoding="utf-8") as fh:
            receipt = json.load(fh)
        return receipt["scope"]["changed"][0]

    def test_fast_receipt_carries_same_per_file_check_keys_as_normal(self):
        tmp_normal = _clean_tmp("fast-route-keys-normal-")
        normal = ttc.case_docs(tmp_normal)
        tmp_fast = _clean_tmp("fast-route-keys-fast-")
        fast = ttc.case_docs_eligible(tmp_fast)
        self.assertEqual(normal["verdict"], "PASS", normal)
        self.assertEqual(fast["verdict"], "PASS", fast)
        normal_entry = self._receipt_entry(normal, tmp_normal, "docs")
        fast_entry = self._receipt_entry(fast, tmp_fast, "docs-eligible")
        self.assertEqual(set(normal_entry.keys()), set(fast_entry.keys()))


class ThePredicateHasOneOwner(unittest.TestCase):
    """CONSOLIDATION (night run 2026-09-09): brother_run.py must decide
    nothing about SAFETY itself any more -- every deny list, every path
    canonicalization rule, lives in fast_path.py alone. Grepped the source
    rather than asserted behaviourally, because the whole point is that
    the CODE for a second predicate is gone, not merely unreachable."""

    def test_brother_run_carries_no_deny_list_or_forbidden_path_tuple(self):
        with open(_br.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for name in ("FAST_ROUTE_DENY_TERMS", "FAST_ROUTE_DENY_MANIFESTS",
                     "FAST_ROUTE_MAX_FILES", "_fast_route_normalize"):
            self.assertNotIn(name, source,
                             "%s: brother_run.py still carries its own "
                             "eligibility rule; fast_path.py must be the "
                             "only owner" % name)

    def test_brother_run_asks_fast_path_eligible(self):
        with open(_br.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("fast_path.eligible(", source)



#: Hostile case 5's worker: writes the DECLARED file (NOTES.md) exactly
#: like an ordinary successful run, then ALSO writes a second, undeclared
#: file. This is deliberately different from UNDECLARED_MODEL above (which
#: never touches the declared file at all, pure substitution): case 5 is
#: the outcome that looked like one file and turned into several, not the
#: outcome that silently became a different single file.
EXPANSION_MODEL = """
    with open("NOTES.md", "w") as fh:
        fh.write("written by the stub model\\n")
    with open("extra.txt", "w") as fh:
        fh.write("unexpected extra file\\n")
    print("stub model wrote NOTES.md and an extra undeclared file")
"""

#: Hostile case 6's worker: instead of the declared NOTES.md, writes a
#: dependency manifest that was never declared at all. FAST_FORBIDDEN_PATHS
#: (fast_path.py) refuses a unit that DECLARES requirements.txt; this
#: proves the other half, an undeclared manifest write a worker makes mid
#: run, is caught the same way any other undeclared write is: by
#: scope_audit at the loop, named by path, never merged.
MANIFEST_UNDECLARED_MODEL = """
    with open("requirements.txt", "w") as fh:
        fh.write("flask\\n")
    print("stub model wrote requirements.txt instead of NOTES.md")
"""

#: Hostile case 7's worker: sleeps well past the window a SIGKILL needs to
#: land mid-unit, then writes exactly like the ordinary DOCS_MODEL. The
#: sleep starts only after the session counter below has already recorded
#: that the worker ran (ttc.stub_env's own _count_prelude is PREPENDED),
#: so a kill during the sleep still leaves one honest "worker" line behind.
SLOW_DOCS_MODEL = """
    import re as _sd_re, sys as _sd_sys, time as _sd_time
    prompt = _sd_sys.argv[-1] if len(_sd_sys.argv) > 1 else ""
    m = _sd_re.search(r"Declared write scope: ([^\\n]+)", prompt)
    _sd_time.sleep(3)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""


def _seed_docs_eligible_repo(tmp):
    """The exact docs-eligible fixture ttc.case_docs_eligible seeds
    (NOTES.md plus scripts/test_notes.py), built by hand here because the
    tests below need to control the WORKER stub, which case_docs_eligible
    does not expose (it always runs to completion with DOCS_MODEL)."""
    repo = tbr.make_repo(tmp)
    scripts_dir = os.path.join(repo, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    with open(os.path.join(repo, "NOTES.md"), "w", encoding="utf-8") as fh:
        fh.write("notes\n")
    with open(os.path.join(scripts_dir, "test_notes.py"), "w",
              encoding="utf-8") as fh:
        fh.write(ttc.SEEDED_NOTES_TEST)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo,
                   check=True)
    return repo


class TheWorkerTouchesMoreThanItDeclared(unittest.TestCase):
    """Hostile case 5: the task expands from one file to several during the
    worker, and case 6: the worker edits a dependency manifest the outcome
    never named. Both drive the SAME fast route AnUndeclaredWriteEscalates
    EitherWay already proves (scope_audit.audit reads only the paths that
    actually changed against what the unit declared, so a declared file
    ALSO being touched changes nothing about how the extra one is caught),
    but neither of those shapes (declared file plus a second undeclared
    one, and a manifest specifically) had its own test until now."""

    def test_expansion_from_one_file_to_two_is_quarantined_naming_the_extra(self):
        tmp = _clean_tmp("fast-route-expand-")
        repo = _seed_docs_eligible_repo(tmp)
        env = ttc.stub_env(tmp, ttc.POISON_DECOMPOSER, EXPANSION_MODEL)
        runs_root = os.path.join(tmp, "runs")
        os.makedirs(runs_root, exist_ok=True)
        outcome = "edit NOTES.md and prove it with scripts/test_notes.py"
        proc = subprocess.run(
            [sys.executable, ttc.BROTHER_RUN, outcome, "--cwd", repo,
             "--runs-root", runs_root], cwd=repo, env=env,
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("QUARANTINE", proc.stdout)
        self.assertIn("never declared: extra.txt", proc.stdout)
        # the declared file was ALSO touched (the worker did real work, it
        # did not merely misbehave), and that must never itself read as an
        # extra undeclared path.
        self.assertNotIn("never declared: NOTES.md", proc.stdout)
        planner, worker = ttc._read_sessions(tmp)
        self.assertEqual(planner, 0, "the fast route must never call the "
                         "decomposer, even on a run that ends refused")

    def test_undeclared_manifest_write_is_quarantined_naming_it_never_proven(self):
        tmp = _clean_tmp("fast-route-manifest-")
        repo = _seed_docs_eligible_repo(tmp)
        env = ttc.stub_env(tmp, ttc.POISON_DECOMPOSER,
                           MANIFEST_UNDECLARED_MODEL)
        runs_root = os.path.join(tmp, "runs")
        os.makedirs(runs_root, exist_ok=True)
        outcome = "edit NOTES.md and prove it with scripts/test_notes.py"
        proc = subprocess.run(
            [sys.executable, ttc.BROTHER_RUN, outcome, "--cwd", repo,
             "--runs-root", runs_root], cwd=repo, env=env,
            capture_output=True, text=True)
        # NEVER REPORTED PROVEN: a QUARANTINE is a refusal, exit 1, and the
        # word this engine reserves for a real delivery ("verified by")
        # must never appear beside it.
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("QUARANTINE", proc.stdout)
        self.assertIn("never declared: requirements.txt", proc.stdout)
        self.assertNotIn("verified by:", proc.stdout)
        planner, worker = ttc._read_sessions(tmp)
        self.assertEqual(planner, 0)


class TheFastRouteSurvivesARealKill(unittest.TestCase):
    """Hostile case 7: SIGKILL the real brother_run.py process tree while
    the fast route's own worker is mid-flight, then run the exact same
    bare invocation again (no --continue, no --resume). Reuses
    product_acceptance's own crash rig helpers, the way test_crash_resume.py
    and scripts/test_brother_run_bare_resume.py already drive a real kill,
    but against a FAST-0 single-unit run rather than a two-unit decomposed
    one, which neither of those files proves."""

    def test_kill_mid_fast_worker_then_bare_resume_lands_once_no_orphan(self):
        tmp = _clean_tmp("fast-route-kill-")
        repo = _seed_docs_eligible_repo(tmp)
        # POISON_DECOMPOSER stands in for DOOR_MODEL_CMD on BOTH
        # invocations below: if the resumed run ever silently switched to
        # the normal, decomposed route it would call this and exit 3
        # loudly, rather than this test having to guess which route ran.
        env = ttc.stub_env(tmp, ttc.POISON_DECOMPOSER, SLOW_DOCS_MODEL)
        runs_root = tmp
        outcome = "edit NOTES.md and prove it with scripts/test_notes.py"

        proc = pa._popen_group(
            [sys.executable, pa.BROTHER_RUN, outcome, "--cwd", repo,
             "--runs-root", runs_root], cwd=repo, env=env)
        deadline = time.time() + 30
        run_dir = pa._find_run_dir(runs_root, deadline)
        self.assertIsNotNone(run_dir, "brother_run never opened a run "
                             "directory before the deadline")
        claims_path = os.path.join(run_dir, "claims.json")
        claim = pa._wait_for_claim(claims_path, "F1", deadline)
        self.assertIsNotNone(claim, "F1 was never durably claimed before "
                             "the deadline, so the kill could not land "
                             "mid-unit")

        # WAIT FOR THE WORKER TO ACTUALLY START, not merely for the claim:
        # claim-before-spawn (the estate's own design) leaves a real gap
        # between "durably claimed" and "worker subprocess running", and
        # SLOW_DOCS_MODEL logs its own "worker" line (ttc._count_prelude)
        # before it ever sleeps. Waiting for that line lands the kill
        # DURING the sleep, mid-unit, rather than in the earlier gap where
        # nothing has run yet at all.
        worker_started_deadline = time.time() + 20
        while time.time() < worker_started_deadline:
            _planner_seen, _worker_seen = ttc._read_sessions(tmp)
            if _worker_seen >= 1:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(
            _worker_seen, 1,
            "the worker never even started before the deadline, so the "
            "kill below could not land mid-unit")

        pa._killpg(proc)

        # THE CRASH LEFT EVIDENCE, NOT SILENCE: the claim is still marked
        # claimed, and the work never landed (the worker was sleeping, not
        # writing, at the moment it died).
        after_kill = pa._read_claims(claims_path)
        self.assertEqual((after_kill.get("F1") or {}).get("state"),
                         "claimed")
        with open(os.path.join(repo, "NOTES.md"), encoding="utf-8") as fh:
            self.assertNotIn("written", fh.read(),
                             "the kill landed too late: the work already "
                             "finished before it could prove anything")

        # Legitimate test surgery, the same one test_bare_invocation_with_
        # the_same_outcome_resumes_the_crashed_run uses: simulate the
        # lease's TTL having elapsed rather than actually waiting it out.
        pa._edit_expires_at(claims_path, "F1", time.time() - 5)

        runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
        run_dirs_before = set(os.listdir(runs_dir))

        # THE BARE RESUME: the exact same outcome sentence, typed again.
        proc2 = tbr.sh([sys.executable, pa.BROTHER_RUN, outcome, "--cwd",
                       repo, "--runs-root", runs_root], env=env)
        out2 = proc2.stdout + proc2.stderr
        self.assertEqual(proc2.returncode, 0, out2)
        self.assertIn("an unfinished run already covers", out2, out2)
        self.assertIn("resuming it", out2, out2)

        self.assertTrue(os.path.exists(os.path.join(repo, "NOTES.md")))
        with open(os.path.join(repo, "NOTES.md"), encoding="utf-8") as fh:
            self.assertIn("written", fh.read())

        # THE SAME ROUTE DECISION: still eligible, planner still skipped.
        # DOOR_MODEL_CMD stayed poisoned for this second call too, so a
        # silent switch to the decomposed route would already have failed
        # the returncode assertion above; the session counter confirms it
        # positively, cumulative across both processes since they share
        # one sessions.log.
        planner, worker = ttc._read_sessions(tmp)
        self.assertEqual(planner, 0, "the resumed run must never call the "
                         "decomposer: the fast route's own decision "
                         "survives the crash, it is never re-asked")
        self.assertGreaterEqual(worker, 2, "the worker must have run once "
                                "before the kill and again on resume")

        # NO DUPLICATED SETTLED WORK: exactly one run directory throughout,
        # and F1 merged into canonical history exactly once, not replayed.
        run_dirs_after = set(os.listdir(runs_dir))
        self.assertEqual(run_dirs_before, run_dirs_after)
        self.assertEqual(len(run_dirs_after), 1, run_dirs_after)
        merge_log = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True,
            text=True).stdout
        merges = (re.findall(r"Brother integrated \S+ from lane/(\S+)",
                             merge_log)
                  + re.findall(r"Merge branch 'lane/([^']+)'", merge_log))
        self.assertEqual(merges.count("F1"), 1, merge_log)

        # NO LIVE ORPHAN CLAIM: the claim is settled, never left "claimed"
        # under a dead owner.
        final_claims = pa._read_claims(claims_path)
        self.assertNotEqual(final_claims["F1"]["state"], "claimed")
        self.assertIn("released_at", final_claims["F1"])

        # ONE NEXT ACTION: the crash left exactly one thing to do (resume
        # this run), and doing it leaves nothing else pending, a further
        # --continue finds no unfinished run at all rather than a second,
        # different one.
        proc3 = tbr.sh([sys.executable, pa.BROTHER_RUN, "--continue",
                       "--cwd", repo, "--runs-root", runs_root], env=env)
        out3 = proc3.stdout + proc3.stderr
        self.assertIn("NO-DATA: no unfinished run found", out3, out3)


class ARetryIsNeverSilent(unittest.TestCase):
    """TEST HARDENING (night run 2026-09-09), driven both ways: the two
    helpers this suite now leans on (_assert_worker_retry_is_recorded,
    _assert_planner_retry_is_recorded) must actually FAIL a session count
    above 1 that the run's own record does not explain, not merely pass
    every real run this suite happens to see today. Without this class,
    "relaxed to >= 1" could have silently become "never checked again"."""

    def test_a_recorded_worker_retry_passes(self):
        _assert_worker_retry_is_recorded(
            self, {"worker_sessions": 2,
                  "retry_record": {"unit_id": "F1",
                                   "attempt_states": ["failed", "done"],
                                   "claim_attempt": 2}})

    def test_a_worker_retry_with_no_trace_at_all_fails_loudly(self):
        with self.assertRaises(AssertionError):
            _assert_worker_retry_is_recorded(
                self, {"worker_sessions": 2, "retry_record": None})

    def test_a_worker_retry_with_an_undercounted_trace_fails_loudly(self):
        """worker_sessions says 2 but the trace only names 1 attempt: the
        exact shape a half-written retry trace would leave."""
        with self.assertRaises(AssertionError):
            _assert_worker_retry_is_recorded(
                self, {"worker_sessions": 2,
                      "retry_record": {"unit_id": "F1",
                                       "attempt_states": ["done"],
                                       "claim_attempt": 2}})

    def test_a_single_worker_session_never_calls_for_evidence(self):
        """No retry, nothing to prove: retry_record absent entirely must
        never fail a run that never retried."""
        _assert_worker_retry_is_recorded(
            self, {"worker_sessions": 1, "retry_record": None})

    def test_a_recorded_planner_retry_passes(self):
        tmp = _clean_tmp("retry-planner-pass-")
        runs_root = os.path.join(tmp, "runs")
        run_dir = os.path.join(runs_root, "docs", "plan", "runs",
                               "20260909T000000-p")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "run.log"), "w",
                 encoding="utf-8") as fh:
            fh.write("door: asking the decomposer (attempt 1 of 3): x\n"
                    "door: asking the decomposer (attempt 2 of 3): x\n")
        _assert_planner_retry_is_recorded(self, runs_root, 2)

    def test_a_planner_retry_with_no_log_line_fails_loudly(self):
        tmp = _clean_tmp("retry-planner-fail-")
        runs_root = os.path.join(tmp, "runs")
        run_dir = os.path.join(runs_root, "docs", "plan", "runs",
                               "20260909T000000-q")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "run.log"), "w",
                 encoding="utf-8") as fh:
            fh.write("nothing about a retry here\n")
        with self.assertRaises(AssertionError):
            _assert_planner_retry_is_recorded(self, runs_root, 2)

    def test_a_single_planner_session_never_calls_for_evidence(self):
        _assert_planner_retry_is_recorded(
            self, "/no/such/runs-root", 1)


if __name__ == "__main__":
    unittest.main()
