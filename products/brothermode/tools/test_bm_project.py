#!/usr/bin/env python3
"""Regression tests for tools/bm_project.py (Loop 2, docs/superpowers/specs/
2026-08-01-loop2-mechanical-commands-design.md, decisions D-1 and D-4).

Every test drives tools/bm_project.py as a real SUBPROCESS against a fresh
tempfile.TemporaryDirectory() root (BROTHERMODE_ROOT), never against this
repo's own store. Row-level verification of the STORED bytes uses
Store.dump(raw=True) -- the same escape hatch test_bm_store.py's own
_dump() helper documents -- rather than this test file's own SQL. That is
a separate thing from what the CLI's own stdout can now assert: since the
loop2 redaction-policy fix, human-readable text output (status, next) and
--raw JSON read through the D-2 accessors with raw=True, so a task id and
title landing correctly in a mechanical command's own words IS something
TestLoop2RedactionPolicy below asserts directly against stdout, not only
against the raw dump. See bm_project.py's own module docstring for the
full account of what raw=True covers and what --json without --raw still
withholds.

Python 3.9, standard library only. Run: python3 tools/test_bm_project.py

No em or en dashes anywhere in this file, its comments, or its output.
"""
import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "bm_store", os.path.join(HERE, "bm_store.py"))
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)
sys.modules["bm_store"] = bs

PROJECT_CLI = os.path.join(HERE, "bm_project.py")
STORE_CLI = os.path.join(HERE, "bm_store.py")

ACTOR = ("--actor-type", "model", "--actor-name", "tester")


def _run(args, root, extra_env=None):
    """Drive bm_project.py as a real subprocess against `root`.
    BROTHERMODE_ROOT is set explicitly on top of the inherited environment
    (never scrubbed of other developer state this repo's own tests don't
    depend on), matching test_bm.py's own _run_threads-style helper."""
    e = dict(os.environ)
    e.pop("BROTHERMODE_ROOT", None)
    e["BROTHERMODE_ROOT"] = root
    if extra_env:
        e.update(extra_env)
    return subprocess.run([sys.executable, PROJECT_CLI] + list(args),
                          cwd=root, capture_output=True, text=True, env=e)


def _init(root):
    e = dict(os.environ)
    e.pop("BROTHERMODE_ROOT", None)
    e["BROTHERMODE_ROOT"] = root
    r = subprocess.run([sys.executable, STORE_CLI, "init"],
                       cwd=root, capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise AssertionError("bm_store.py init failed: %s" % r.stderr)
    return r


def _raw_dump(root):
    """The unredacted store contents, for asserting what actually landed,
    never for driving assertions about what a founder would SEE (that is
    what the CLI's own stdout is for)."""
    store = bs.Store(root, create=False)
    try:
        return store.dump(raw=True)
    finally:
        store.close()


def _docstring_constant_ids(tree):
    """id() of every ast.Constant node ast.get_docstring would return the
    value of: the first statement of a Module/FunctionDef/
    AsyncFunctionDef/ClassDef body, when it is a bare string expression.
    Docstrings are prose a human reads, never an argument any code
    executes, and this file's own module docstring legitimately uses
    ordinary English words ('an update', 'select only the questions
    that...') a blind scan cannot tell apart from SQL. Comments need no
    equivalent exclusion: Python's own tokenizer discards them before the
    AST exists, so they were never a node a walk could visit in the first
    place."""
    ids = set()
    doc_holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                   ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, doc_holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


class TestNoSQLGuard(unittest.TestCase):
    """D-1's flip condition, made executable: the moment bm_project.py
    needs a query the store does not already offer, it has become a
    second writer and must be folded back into bm_store.py.

    C7 (release-closure loop2 refuter fixes, from the PLAUSIBLE set,
    fixed anyway): the original guard was `re.findall(r"\\b(SELECT|
    INSERT|UPDATE|DELETE)\\b", source)` over the file's RAW TEXT,
    uppercase only. Two trivial bypasses evaded it without anything
    exotic: write the query in lowercase (`"select * from tasks"` is
    legal SQL and never matches an uppercase-only pattern), or split one
    keyword across two adjacent string literals (`"SEL" "ECT"` -- Python
    joins those into a single string at parse time, but they are never
    ONE contiguous word in the raw source a regex reads). Making the old
    pattern merely case-insensitive reopens the false positive its own
    comment already warned about (this file's legitimate lowercase
    `dict.update()` and `project['updated_at']`), and, proven below,
    trips on ordinary English prose in this file's own docstrings ("an
    update", "select only the questions..."). The fix is a smaller
    haystack, not a smarter pattern: parse the file with `ast` and check
    only STRING LITERAL VALUES (ast.Constant, case insensitive) for the
    keyword, excluding docstrings. Adjacent string-literal concatenation
    is already ONE Constant node with the joined value by the time
    ast.parse returns, which closes that bypass for free; a bare Python
    identifier like `update` or `updated_at` is a Name/Attribute node,
    never a string constant, so it is never even examined."""

    _SQL_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|PRAGMA)\b",
                         re.IGNORECASE)

    def test_bm_project_never_issues_its_own_sql(self):
        with io.open(os.path.join(HERE, "bm_project.py"),
                     encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_project.py")
        skip = _docstring_constant_ids(tree)
        hits = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in skip
                    and self._SQL_RE.search(node.value)):
                hits.append((node.lineno, node.value))
        self.assertEqual(
            hits, [],
            "bm_project.py must never contain a string literal shaped "
            "like SQL (found %r); a query the store does not already "
            "offer means this file has become a second writer and the "
            "query belongs in bm_store.py instead (D-1's flip "
            "condition)." % hits)

    def test_bm_project_never_imports_sqlite3(self):
        """Even a keyword-free query needs something able to run it.
        This file has no legitimate reason to import sqlite3 directly:
        every query it needs already goes through bs, the loaded
        bm_store module, and its Store service methods."""
        with io.open(os.path.join(HERE, "bm_project.py"),
                     encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_project.py")
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(a.name for a in node.names
                                 if a.name.split(".")[0] == "sqlite3")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "sqlite3":
                    offenders.append(node.module)
        self.assertEqual(
            offenders, [],
            "bm_project.py must never import sqlite3 directly (found "
            "%r); every query it needs already goes through bs, the "
            "loaded bm_store module." % offenders)

    def test_calibrated_old_guard_missed_a_lowercase_or_split_bypass(self):
        """CALIBRATION. Reproduces the pre-fix guard's own logic against a
        synthetic snippet standing in for what a change to bm_project.py
        could slip in, proving it would have said nothing was wrong, then
        proves the current AST-based guard catches both bypasses in the
        same snippet."""
        bypass_snippet = (
            'query = "sel" "ect * from tasks"\n'   # adjacent-literal split
            'other = "update tasks set status=?"\n'  # lowercase, whole word
        )
        old_style_hits = re.findall(
            r"\b(SELECT|INSERT|UPDATE|DELETE)\b", bypass_snippet)
        self.assertEqual(
            old_style_hits, [],
            "calibration sanity: the OLD guard must find nothing in this "
            "snippet, or it does not actually demonstrate the bypass")
        tree = ast.parse(bypass_snippet)
        hits = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and self._SQL_RE.search(n.value)]
        self.assertEqual(
            len(hits), 2,
            "the AST-based guard must catch both the split literal and "
            "the lowercase keyword the old regex missed (got %r)" % hits)


class TestRefusals(unittest.TestCase):
    """The ten-state law refuses exactly in schema.transition's own words,
    never restated by this CLI, and 'done' is refused BY NAME."""

    def _task_in_planned(self, root):
        _init(root)
        r = _run(["start", "--project-id", "proj1", "--name", "Acme Rescue"]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "add", "--project-id", "proj1",
                  "--task-id", "task1", "--title", "Do the thing"]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_illegal_transition_refuses_with_schema_text(self):
        with tempfile.TemporaryDirectory() as root:
            self._task_in_planned(root)
            # planned -> verified skips every stage in between; legal moves
            # from planned are ('ready',) only.
            r = _run(["task", "transition", "--task-id", "task1",
                      "--to", "verified", "--reason", "skipping ahead"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("illegal transition", r.stderr)
            self.assertIn("'planned' to 'verified'", r.stderr)
            self.assertIn("Legal moves from 'planned': ready", r.stderr)
            # Refused, so nothing moved: still planned.
            raw = _raw_dump(root)
            tasks = {t["task_id"]: t for t in raw["tasks"]}
            self.assertEqual(tasks["task1"]["status"], "planned")

    def test_a_task_cannot_be_born_past_the_start_of_the_lifecycle(self):
        """M4, and the reason it matters is M2.

        REPRODUCED before this test existed, in a throwaway root, three
        commands and nothing else:

            task add --project-id px --task-id t1 --title "do thing" \\
                     --status closed          -> "added task t1"
            deliver  --project-id px          -> "delivered px: all 1
                                                  task(s) closed", exit 0

        No goal, no acceptance check, no build, no review, no evidence, and
        the product reports a delivered project. The transition law was never
        the hole: planned to verified is refused correctly, and this test's
        sibling above proves it. The hole is that `--status` chose the state a
        task was BORN in, so a task could start past every gate rather than
        walk through them.

        The guard is on BIRTH, one place all callers route through, rather
        than a second gate inside deliver: deliver's own rule (every task at
        the terminal state) was doing its job on the evidence it had.
        """
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "px", "--name", "Probe"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            for illegal in ("closed", "verified", "delivered", "accepted",
                            "monitored", "active", "awaiting review"):
                r = _run(["task", "add", "--project-id", "px",
                          "--task-id", "t-" + illegal.replace(" ", "-"),
                          "--title", "do thing", "--status", illegal]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 1,
                                 "birth in %r was accepted: %s" % (illegal, r.stdout))
                self.assertIn("cannot be created in", r.stderr, illegal)
                self.assertIn(illegal, r.stderr, illegal)
            raw = _raw_dump(root)
            self.assertEqual([], raw["tasks"],
                             "a refused birth still wrote a task row")

    def test_the_two_legal_birth_states_still_work(self):
        """The other half, so the guard cannot be tightened into a wall.
        `planned` is the default and `ready` is the state the guided flow
        needs, because `next` serves only ready tasks."""
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            _run(["start", "--project-id", "px", "--name", "Probe"]
                 + list(ACTOR), root)
            for legal in ("planned", "ready"):
                r = _run(["task", "add", "--project-id", "px",
                          "--task-id", "ok-" + legal, "--title", "do thing",
                          "--status", legal] + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "add", "--project-id", "px",
                      "--task-id", "default", "--title", "do thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            got = {t["task_id"]: t["status"] for t in raw["tasks"]}
            self.assertEqual({"ok-planned": "planned", "ok-ready": "ready",
                              "default": "planned"}, got)

    def test_done_state_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as root:
            self._task_in_planned(root)
            r = _run(["task", "transition", "--task-id", "task1",
                      "--to", "done", "--reason", "marking it done"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("refused by name", r.stderr)
            self.assertIn("Done is not a valid state", r.stderr)
            raw = _raw_dump(root)
            tasks = {t["task_id"]: t for t in raw["tasks"]}
            self.assertEqual(tasks["task1"]["status"], "planned")

    def test_task_transition_requires_reason(self):
        with tempfile.TemporaryDirectory() as root:
            self._task_in_planned(root)
            r = _run(["task", "transition", "--task-id", "task1",
                      "--to", "ready"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)

    def test_start_requires_actor_name(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "proj1", "--name", "X"], root)
            self.assertEqual(r.returncode, 2)

    def test_unknown_command_is_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["bogus"], root)
            self.assertEqual(r.returncode, 2)

    def test_status_on_unknown_project_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["status", "--project-id", "nope"], root)
            self.assertEqual(r.returncode, 1)

    def test_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run(["--help"], root)
            self.assertEqual(r.returncode, 0)
            self.assertIn("commands:", r.stdout)


class TestNextNamesTheWayOut(unittest.TestCase):
    """M3. `next` reading only 'ready' is correct and is NOT what changed:
    the protocol defines that state as dependency-satisfied. What was wrong
    is that a task is born 'planned', nothing moves it, and the dead end
    named no exit. Reproduced before this existed: create a project, add one
    task, ask for the next step, and get "0 task(s) currently in state
    'ready'" with nothing else."""

    def _project_with_one_task(self, root):
        _init(root)
        r = _run(["start", "--project-id", "px", "--name", "Probe"]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "add", "--project-id", "px", "--task-id", "t1",
                  "--title", "do the thing"] + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_dead_end_now_names_the_command_that_ends_it(self):
        with tempfile.TemporaryDirectory() as root:
            self._project_with_one_task(root)
            r = _run(["next", "--project-id", "px"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("0 task(s) currently in state 'ready'", r.stdout)
            self.assertIn("still 'planned'", r.stdout)
            self.assertIn("task transition --task-id t1 --to ready", r.stdout)

    def test_following_that_command_makes_next_return_the_task(self):
        """The half that proves the advice is not merely well worded."""
        with tempfile.TemporaryDirectory() as root:
            self._project_with_one_task(root)
            r = _run(["task", "transition", "--task-id", "t1", "--to",
                      "ready", "--reason", "nothing blocks it"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["next", "--project-id", "px"], root)
            self.assertIn("next: t1 - do the thing", r.stdout)

    def test_a_project_with_no_tasks_at_all_says_only_the_plain_line(self):
        """No task means nothing to advise, so the extra guidance must not
        appear and invent a task that does not exist."""
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            _run(["start", "--project-id", "px", "--name", "Probe"]
                 + list(ACTOR), root)
            r = _run(["next", "--project-id", "px"], root)
            self.assertIn("0 task(s) currently in state 'ready'", r.stdout)
            self.assertNotIn("still 'planned'", r.stdout)

    def test_json_output_is_unchanged_by_this(self):
        """The guidance is display only. --json is an export surface and a
        consumer parsing it must see exactly what it saw before."""
        with tempfile.TemporaryDirectory() as root:
            self._project_with_one_task(root)
            r = _run(["next", "--project-id", "px", "--json"], root)
            payload = json.loads(r.stdout)
            self.assertEqual({"candidate_count", "picked"}, set(payload))
            self.assertEqual(0, payload["candidate_count"])
            self.assertIsNone(payload["picked"])


class TestScriptedFirstProject(unittest.TestCase):
    """D-4: the gate is executable. This drives every one of the seven
    command surfaces through subprocess against a temp root, and asserts
    rows, an attribution row per mutation, and that CANVAS.md regenerates
    byte-stable from rows."""

    def test_scripted_first_project_end_to_end(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)

            # -- start: project row, no tasks/forecast yet --------------
            r = _run(["start", "--project-id", "proj1",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            started = json.loads(r.stdout)
            self.assertEqual(started["project_id"], "proj1")
            self.assertEqual(started["task_ids"], [])

            # -- status: rows only, machine readable ---------------------
            r = _run(["status", "--project-id", "proj1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            status = json.loads(r.stdout)
            self.assertEqual(status["tasks_by_state"]["planned"], [])
            self.assertIsNone(status["latest_forecast"])

            # -- task add -------------------------------------------------
            r = _run(["task", "add", "--project-id", "proj1",
                      "--task-id", "task1", "--title", "Do the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["task_id"], "task1")

            # planned -> ready
            r = _run(["task", "transition", "--task-id", "task1",
                      "--to", "ready", "--reason", "deps clear"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["status"], "ready")

            # -- next: recommends the one ready task ----------------------
            r = _run(["next", "--project-id", "proj1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            nxt = json.loads(r.stdout)
            self.assertEqual(nxt["candidate_count"], 1)
            self.assertIsNotNone(nxt["picked"])

            # task start: ready -> active
            r = _run(["task", "start", "--task-id", "task1",
                      "--reason", "beginning"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["status"], "active")

            # active -> awaiting review
            r = _run(["task", "transition", "--task-id", "task1",
                      "--to", "awaiting review", "--reason", "work done"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)

            # -- review: evidence recorded, awaiting review -> verified ---
            r = _run(["review", "task1", "--project-id", "proj1",
                      "--kind", "test", "--ref", "tools/test_all.py",
                      "--reason", "checks passed"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            review_out = json.loads(r.stdout)
            self.assertEqual(review_out["status"], "verified")
            evidence_id = review_out["evidence_id"]

            # -- deliver refuses: the task has not reached 'closed' -------
            r = _run(["deliver", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("terminal", r.stderr)

            # --partial delivers anyway
            r = _run(["deliver", "--project-id", "proj1", "--partial",
                      "--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["partial"])

            # walk the rest of the lifecycle to the terminal state
            for state, reason in (
                    ("accepted", "owner approved"),
                    ("delivered", "handed off"),
                    ("monitored", "watching"),
                    ("closed", "monitoring window over")):
                r = _run(["task", "transition", "--task-id", "task1",
                          "--to", state, "--reason", reason]
                         + list(ACTOR) + ["--out-json"], root)
                self.assertEqual(r.returncode, 0, r.stderr)

            # deliver now succeeds without --partial
            r = _run(["deliver", "--project-id", "proj1", "--out-json"],
                     root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(json.loads(r.stdout)["partial"])

            # -- assert rows exist, via the sanctioned raw escape hatch ---
            raw = _raw_dump(root)
            projects = {p["project_id"]: p for p in raw["projects"]}
            self.assertIn("proj1", projects)
            self.assertEqual(projects["proj1"]["name"], "Acme Rescue")
            self.assertEqual(projects["proj1"]["goal"], "Ship the thing")

            tasks = {t["task_id"]: t for t in raw["tasks"]}
            self.assertIn("task1", tasks)
            self.assertEqual(tasks["task1"]["status"], "closed")
            self.assertEqual(tasks["task1"]["title"], "Do the thing")

            evidence_rows = [e for e in raw["evidence"]
                            if e["evidence_id"] == evidence_id]
            self.assertEqual(len(evidence_rows), 1)
            self.assertEqual(evidence_rows[0]["subject_type"], "task")
            self.assertEqual(evidence_rows[0]["subject_id"], "task1")
            self.assertEqual(evidence_rows[0]["kind"], "test")

            # -- an attribution row per mutation ---------------------------
            # upsert_project (1) + create_task (1) + transition_task x8
            # (ready, active, 'awaiting review', verified[via review],
            # accepted, delivered, monitored, closed) + add_evidence (1,
            # from review) = 11. deliver itself calls no Store mutation
            # method (there is none for "delivery packet generated"), so
            # it contributes none, on purpose (see bm_project.py's own
            # comment at cmd_deliver).
            attributions = [a for a in raw["attribution"]
                            if a["project_id"] == "proj1"]
            event_types = [a["event_type"] for a in attributions]
            self.assertEqual(event_types.count("project.upserted"), 1)
            self.assertEqual(event_types.count("task.created"), 1)
            self.assertEqual(event_types.count("task.transitioned"), 8)
            self.assertEqual(event_types.count("evidence.added"), 1)
            self.assertEqual(len(attributions), 11)
            for a in attributions:
                self.assertEqual(a["actor_name"], "tester")
                self.assertEqual(a["actor_type"], "model")

            # -- CANVAS.md regenerates byte-stable from rows ---------------
            # CANVAS.md is only ever (re)written by `start` (D-3): the CLI
            # calls above (task/review/deliver) never touch it, so the copy
            # on disk right now still reflects the FIRST `start` call at
            # the very top of this test, before task1 even existed. Prime
            # it against the CURRENT rows once (unasserted), THEN take the
            # two comparable snapshots, or this compares two different
            # states of the world rather than two renders of the same one.
            canvas_path = os.path.join(root, "CANVAS.md")
            r = _run(["start", "--project-id", "proj1",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(canvas_path, encoding="utf-8") as fh:
                canvas1 = fh.read()
            self.assertIn("BEGIN GENERATED BROTHERMODE CANVAS", canvas1)
            self.assertIn("closed", canvas1)

            r = _run(["start", "--project-id", "proj1",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(canvas_path, encoding="utf-8") as fh:
                canvas2 = fh.read()
            self.assertEqual(
                canvas1, canvas2,
                "CANVAS.md must regenerate byte-stable from the same rows")

            # And a THIRD regeneration: a two-run comparison alone would
            # have missed a real compounding bug found while building this
            # suite (each re-splice into an already-marked file grew the
            # file by one blank line, forever); this pins the fixed point.
            r = _run(["start", "--project-id", "proj1",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(canvas_path, encoding="utf-8") as fh:
                canvas3 = fh.read()
            self.assertEqual(canvas2, canvas3)

            # -- human prose outside the markers survives regeneration ----
            with io.open(canvas_path, "a", encoding="utf-8") as fh:
                fh.write("\nA note a human wrote by hand.\n")
            r = _run(["start", "--project-id", "proj1",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(canvas_path, encoding="utf-8") as fh:
                canvas4 = fh.read()
            self.assertIn("A note a human wrote by hand.", canvas4)
            self.assertEqual(
                canvas4.count("A note a human wrote by hand."), 1)

            # -- DELIVERY-PACKET.md exists and is a generated view --------
            packet_path = os.path.join(root, "DELIVERY-PACKET.md")
            self.assertTrue(os.path.isfile(packet_path))
            with io.open(packet_path, encoding="utf-8") as fh:
                packet = fh.read()
            self.assertIn("BEGIN GENERATED BROTHERMODE DELIVERY PACKET",
                          packet)


class TestGuidedStartWalkthroughLeavesAReadyTask(unittest.TestCase):
    """M8 (docs/plan/QUEUE.json): "the guided walkthrough stops at the
    project brief: no skill, command or reference tells anyone to create
    tasks, so next reads a queue the flow never fills". skills/start/
    SKILL.md's own section "The task that keeps the walkthrough from
    dead-ending" now names the fix in prose (a `task add ... --status
    ready` right after the brief); this test is the missing other half:
    proof that driving the SAME commands the skill names actually leaves
    `next` something to serve, and a pin on the doc so the load-bearing
    `--status ready` flag cannot silently drift out of it again."""

    def test_skill_doc_still_names_status_ready_on_the_task_add_command(self):
        """Regression pin on the prose itself: M8 was closed by adding
        `--status ready` to the task-add command SKILL.md names (task
        born 'planned' is invisible to `next`, per M3/M4's own lesson).
        A future edit that drops that one flag reopens the dead end
        silently, so the doc text is asserted, not just the behavior."""
        skill_path = os.path.join(
            os.path.dirname(HERE), "skills", "start", "SKILL.md")
        with io.open(skill_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("task add", text)
        self.assertIn("--status ready", text)

    def test_scripted_walkthrough_leaves_next_something_to_serve(self):
        """REPRODUCED pre-fix (docs/NORTH-STAR-CHAIN.md's own M3 finding):
        a project brief alone (`start`, no task) leaves a task born
        'planned' the moment one IS added without `--status ready`, and
        `next` reads only 'ready', so the walkthrough dead-ends with
        nothing to hand back. This scripts the same commands SKILL.md's
        own "mechanical command that records the brief" and "task that
        keeps the walkthrough from dead-ending" sections name, in order,
        and asserts `next` picks the task at the end."""
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "px", "--name", "First Run",
                      "--goal", "Ship the thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            # `next` right after the brief: no task exists yet, the one
            # real dead end the skill's own text names.
            r = _run(["next", "--project-id", "px", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            before = json.loads(r.stdout)
            self.assertEqual(before["candidate_count"], 0)
            self.assertIsNone(before["picked"])
            # The exact mechanical command skills/start/SKILL.md names,
            # actor renamed to match this suite's convention.
            r = _run(["task", "add", "--project-id", "px",
                      "--title", "Wire up the first real piece",
                      "--status", "ready"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["next", "--project-id", "px", "--json", "--raw"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            after = json.loads(r.stdout)
            self.assertEqual(after["candidate_count"], 1)
            self.assertIsNotNone(after["picked"])
            self.assertEqual(after["picked"]["title"],
                             "Wire up the first real piece")


class TestReleaseClosureLoop2RefuterFixes(unittest.TestCase):
    """Regression coverage for the twelve confirmed loop2-refuter
    findings this file's own correctness fixes (C1-C6) touch. Each test
    below fails against the pre-fix code and passes against the fix."""

    def _started(self, root, project_id="proj1", name="Acme Rescue",
                extra=()):
        _init(root)
        r = _run(["start", "--project-id", project_id, "--name", name]
                 + list(extra) + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_review_refused_transition_leaves_no_orphan_evidence(self):
        """C1: review used to call add_evidence and transition_task as
        two SEPARATE transactions, so a refused transition still left the
        evidence from the first call sitting on disk. review_task now
        runs both in ONE transaction."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["task", "add", "--project-id", "proj1",
                      "--task-id", "task1", "--title", "Do the thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            # review defaults --to verified; legal moves from 'planned'
            # are ('ready',) only, so this is refused.
            r = _run(["review", "task1", "--project-id", "proj1",
                      "--kind", "test", "--ref", "x", "--reason", "trying"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            raw = _raw_dump(root)
            self.assertEqual(
                [e for e in raw["evidence"] if e["subject_id"] == "task1"],
                [], "a refused review transition must write no evidence row")

    def test_review_refuses_evidence_for_a_nonexistent_task(self):
        """C2: add_evidence used to validate nothing about its subject.
        Reviewing a task id that names nothing used to insert an evidence
        row for it anyway (the transition call that came after was what
        actually failed), leaving an orphan evidence row for a subject
        that never existed."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["review", "nope", "--project-id", "proj1",
                      "--reason", "trying"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            raw = _raw_dump(root)
            self.assertEqual(
                [e for e in raw["evidence"] if e["subject_id"] == "nope"],
                [], "evidence for a nonexistent subject must never be "
                    "written")

    def test_review_refuses_evidence_when_project_id_mismatches_the_tasks_own_project(self):
        """C3: evidence carries no project_id column of its own; without
        a cross-check, evidence for a task in one project could be filed
        under a different project's id."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            self._started(root, project_id="proj2", name="Other",
                          extra=("--allow-second",))
            r = _run(["task", "add", "--project-id", "proj2",
                      "--task-id", "task2", "--title", "Do the other thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            # task2 belongs to proj2; review claims proj1.
            r = _run(["review", "task2", "--project-id", "proj1",
                      "--reason", "trying"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            raw = _raw_dump(root)
            self.assertEqual(
                [e for e in raw["evidence"] if e["subject_id"] == "task2"],
                [], "a project-mismatched review must write no evidence "
                    "row")

    def test_deliver_refuses_a_project_with_zero_tasks_even_with_partial(self):
        """C4: deliver used to succeed on a project with zero tasks
        (0 non-terminal out of 0 total looks, wrongly, like nothing is
        outstanding); --partial must not override this."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["deliver", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("zero tasks", r.stderr)
            r = _run(["deliver", "--project-id", "proj1", "--partial"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("zero tasks", r.stderr)

    def test_deliver_refuses_a_hollow_project_even_when_every_task_is_closed(self):
        """M2: deliver's own two checks (nonzero tasks, every task at the
        terminal state) both pass on a project that never had a goal,
        never had a single piece of evidence filed, and never had a task
        actually pass review: `task transition` walks a task through the
        WHOLE lifecycle to 'closed' with no evidence required at all, only
        `review` files evidence, and nothing forces a caller to use it.

        REPRODUCED pre-fix, in a throwaway root, after M4 closed the
        birth-state hole so this could not be shortcut at `task add`
        instead: start a project with no --goal, `task add` (born
        'planned', legal), then `task transition` it straight through
        ready, active, 'awaiting review', verified, accepted, delivered,
        monitored, closed, six transitions, zero evidence rows, `review`
        never called. `deliver --project-id px` still printed "delivered
        px: all 1 task(s) closed" and exited 0."""
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "px", "--name", "Probe"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "add", "--project-id", "px",
                      "--task-id", "t1", "--title", "do thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            for state, reason in (
                    ("ready", "deps clear"), ("active", "beginning"),
                    ("awaiting review", "work finished"),
                    ("verified", "skip the review command"),
                    ("accepted", "owner approved"),
                    ("delivered", "handed off"), ("monitored", "watching"),
                    ("closed", "monitoring window over")):
                r = _run(["task", "transition", "--task-id", "t1",
                          "--to", state, "--reason", reason]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["deliver", "--project-id", "px"], root)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("no goal", r.stderr)
            self.assertIn("no build evidence", r.stderr)
            self.assertIn("no review", r.stderr)
            # nothing was written: no CANVAS/DELIVERY-PACKET regeneration
            # from a delivery that was refused.
            self.assertFalse(
                os.path.isfile(os.path.join(root, "DELIVERY-PACKET.md")))
            # An explicit --partial is a different act: the human declares
            # the delivery incomplete, so it proceeds, and the same holes
            # are printed so a partial can never read as silently clean.
            # Pinned 2026-08-20 after the full gate caught the first M2
            # guard also refusing the CLI's legitimate partial-delivery
            # flow (test_deliver_partial_writes_the_packet).
            r = _run(["deliver", "--project-id", "px", "--partial"], root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("holes on record", r.stderr)
            self.assertIn("no goal", r.stderr)
            self.assertTrue(
                os.path.isfile(os.path.join(root, "DELIVERY-PACKET.md")))

    def test_start_refuses_a_second_project_without_allow_second(self):
        """C5: two different project_ids sharing one root would clobber
        CANVAS.md and the delivery packet in turn."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["start", "--project-id", "proj2", "--name", "Other"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("proj1", r.stderr)
            # Re-running start on the SAME project_id is an update, never
            # "a second project", and is never refused.
            r = _run(["start", "--project-id", "proj1", "--name",
                      "Acme Rescue"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_start_allow_second_switches_canvas_and_packet_to_project_scoped_names(self):
        """C5: once a root genuinely holds more than one project,
        CANVAS.md/DELIVERY-PACKET.md must not be shared filenames."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            self.assertTrue(os.path.isfile(os.path.join(root, "CANVAS.md")))
            # M2: deliver now refuses a hollow project (no goal, no
            # evidence, no task ever reviewed), so proj2 needs a goal and
            # one reviewed task to reach the file-naming assertion this
            # test actually cares about, exactly like proj1 already gets
            # from ACME's own --goal elsewhere in this suite.
            self._started(root, project_id="proj2", name="Other",
                          extra=("--allow-second", "--goal", "Fix it"))
            self.assertTrue(
                os.path.isfile(os.path.join(root, "CANVAS-proj2.md")),
                "a second project's canvas must not clobber CANVAS.md")
            r = _run(["task", "add", "--project-id", "proj2",
                      "--task-id", "task2", "--title", "Do the other thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "transition", "--task-id", "task2",
                      "--to", "ready", "--reason", "deps clear"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "start", "--task-id", "task2",
                      "--reason", "beginning"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "transition", "--task-id", "task2",
                      "--to", "awaiting review", "--reason", "work done"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["review", "task2", "--project-id", "proj2",
                      "--reason", "checked it"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["deliver", "--project-id", "proj2", "--partial"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(
                os.path.isfile(
                    os.path.join(root, "DELIVERY-PACKET-proj2.md")),
                "a second project's delivery packet must not clobber "
                "DELIVERY-PACKET.md")

    def test_next_ties_on_priority_by_insertion_order_not_task_id(self):
        """C6: tasks carry no created_at; the tie break used to be
        task_id order (a random hex uuid), which next's own WHY text
        misdescribed as 'earliest created'. Task ids below are chosen so
        alphabetical order would pick the WRONG one: 'zzz...' is added
        FIRST but sorts LAST alphabetically."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            for task_id, title in (("zzz-added-first", "First in"),
                                   ("aaa-added-second", "Second in")):
                r = _run(["task", "add", "--project-id", "proj1",
                          "--task-id", task_id, "--title", title]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
                r = _run(["task", "transition", "--task-id", task_id,
                          "--to", "ready", "--reason", "deps clear"]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)

            r = _run(["next", "--project-id", "proj1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            nxt = json.loads(r.stdout)
            self.assertEqual(
                nxt["picked"]["task_id"], "zzz-added-first",
                "same priority (both blank): the tie break must be "
                "insertion order, not task_id order")

            r = _run(["next", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("added first", r.stdout)
            self.assertNotIn("earliest created", r.stdout)


class TestLoop2RedactionPolicy(unittest.TestCase):
    """The loop2 redaction-policy fix, asserted against the CLI's own
    stdout rather than the raw dump escape hatch: _DUMP_SAFE_COLUMNS now
    lists identifiers, enums and timestamps for the schema-12 tables, and
    this file's human-readable text output (status, next) reads through
    raw=True (local display is not an export; see bm_project.py's own
    module docstring), so a task id and a title can finally be named in a
    mechanical command's own words. --json without --raw stays on the
    OTHER side of that line: it is the export surface, and prose still
    comes back withheld there by default, exactly like bm_store.py's own
    dump."""

    def _seeded_project(self, root):
        _init(root)
        r = _run(["start", "--project-id", "proj1",
                  "--name", "Acme Rescue", "--goal", "Ship the thing"]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "add", "--project-id", "proj1",
                  "--task-id", "task1", "--title", "Fix the leaky pipe"]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "transition", "--task-id", "task1",
                  "--to", "ready", "--reason", "deps clear"]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_and_next_text_output_name_ids_and_titles_via_raw(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)

            r = _run(["status", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("proj1", r.stdout)
            self.assertIn("Acme Rescue", r.stdout)

            r = _run(["next", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("task1", r.stdout)
            self.assertIn("Fix the leaky pipe", r.stdout)

    def test_json_without_raw_keeps_prose_withheld_raw_reveals_it(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)

            r = _run(["status", "--project-id", "proj1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            status = json.loads(r.stdout)
            self.assertEqual(status["project"]["project_id"], "proj1")
            self.assertTrue(
                status["project"]["name"].startswith("[WITHHELD"),
                status["project"])
            self.assertNotIn("Acme Rescue", r.stdout)

            r = _run(["status", "--project-id", "proj1", "--json", "--raw"],
                     root)
            self.assertEqual(r.returncode, 0, r.stderr)
            status_raw = json.loads(r.stdout)
            self.assertEqual(status_raw["project"]["name"], "Acme Rescue")

            r = _run(["next", "--project-id", "proj1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            nxt = json.loads(r.stdout)
            self.assertEqual(nxt["picked"]["task_id"], "task1")
            self.assertTrue(
                nxt["picked"]["title"].startswith("[WITHHELD"),
                nxt["picked"])
            self.assertNotIn("Fix the leaky pipe", r.stdout)

            r = _run(["next", "--project-id", "proj1", "--json", "--raw"],
                     root)
            self.assertEqual(r.returncode, 0, r.stderr)
            nxt_raw = json.loads(r.stdout)
            self.assertEqual(nxt_raw["picked"]["title"], "Fix the leaky pipe")

    def test_deliver_without_raw_withholds_prose_raw_reveals_it(self):
        """DELIVERY-PACKET.md is an export by the same policy as --json,
        not a local working page like CANVAS.md (see render_delivery_
        packet's own docstring): `deliver` without --raw must write the
        goal/title withheld, and `deliver --raw` must write them in the
        clear, the same both-directions shape
        test_json_without_raw_keeps_prose_withheld_raw_reveals_it above
        proves for --json, so this can never pass against a function that
        withholds (or reveals) unconditionally."""
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            packet_path = os.path.join(root, "DELIVERY-PACKET.md")

            r = _run(["deliver", "--project-id", "proj1", "--partial"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(packet_path, encoding="utf-8") as fh:
                withheld = fh.read()
            self.assertIn("[WITHHELD", withheld)
            self.assertNotIn("Acme Rescue", withheld)
            self.assertNotIn("Fix the leaky pipe", withheld)

            r = _run(["deliver", "--project-id", "proj1", "--partial",
                      "--raw"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(packet_path, encoding="utf-8") as fh:
                revealed = fh.read()
            self.assertIn("Acme Rescue", revealed)
            self.assertIn("Fix the leaky pipe", revealed)


class TestForecastAndAlerts(unittest.TestCase):
    """D-1 (Loop 5 design, docs/superpowers/specs/2026-08-01-loop4-close-
    and-loop5-design.md): forecast add/show, alert raise/resolve/list, and
    the two grown status sections."""

    def _started(self, root, project_id="proj1", name="Acme Rescue"):
        _init(root)
        r = _run(["start", "--project-id", project_id, "--name", name]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_forecast_add_requires_a_token_range(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["forecast", "add", "--project-id", "proj1",
                      "--min-duration", "two hours",
                      "--likely-duration", "three hours",
                      "--max-duration", "four hours",
                      "--confidence", "medium"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("token-range", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["forecasts"], [])

    def test_forecast_add_refuses_a_point_estimate_with_no_basis(self):
        """D-1: a single point estimate (min = likely = max, no --basis)
        is refused, quoting the forecasting rule in plain words."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["forecast", "add", "--project-id", "proj1",
                      "--min-duration", "two hours",
                      "--likely-duration", "two hours",
                      "--max-duration", "two hours",
                      "--input-token-range", "five k",
                      "--confidence", "medium"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("ranges, never points", r.stderr)
            self.assertIn("false promise", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(
                raw["forecasts"], [],
                "a refused point-estimate forecast must write no row")

    def test_forecast_add_accepts_a_point_duration_with_a_stated_basis(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["forecast", "add", "--project-id", "proj1",
                      "--min-duration", "two hours",
                      "--likely-duration", "two hours",
                      "--max-duration", "two hours",
                      "--input-token-range", "five k",
                      "--confidence", "high",
                      "--basis", "fixed-fee contract with a set window"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_forecast_add_never_edits_a_prior_forecast_row(self):
        """D-4 through the CLI door: forecasts append, they are never
        edited (already enforced at the store layer by test_bm_store.py's
        test_add_forecast_never_edits_a_prior_forecast_row; this pins the
        same contract through tools/bm_project.py itself)."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            for basis in ("first", "second"):
                r = _run(["forecast", "add", "--project-id", "proj1",
                          "--min-duration", "two hours",
                          "--likely-duration", "three hours",
                          "--max-duration", "four hours",
                          "--input-token-range", "five k",
                          "--confidence", "medium", "--basis", basis]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(len(raw["forecasts"]), 2)

    def test_forecast_show_reports_ranges_confidence_and_prior_count(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["forecast", "show", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no forecast recorded", r.stdout)
            for basis in ("first", "second"):
                r = _run(["forecast", "add", "--project-id", "proj1",
                          "--min-duration", "two hours",
                          "--likely-duration", "three hours",
                          "--max-duration", "four hours",
                          "--input-token-range", "five k",
                          "--confidence", "medium", "--basis", basis]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["forecast", "show", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("two hours to four hours (likely three hours)",
                          r.stdout)
            self.assertIn("confidence: medium", r.stdout)
            self.assertIn("prior forecasts: 1", r.stdout,
                          "the second add's SHOW must count the first as "
                          "one prior forecast")

    def test_alert_raise_resolve_list_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["alert", "raise", "--project-id", "proj1",
                      "--severity", "high", "--message", "queue backing up"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            alert1 = json.loads(r.stdout)["alert_id"]
            r = _run(["alert", "raise", "--project-id", "proj1",
                      "--severity", "critical", "--message", "db offline",
                      "--requires-human"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            alert2 = json.loads(r.stdout)["alert_id"]

            r = _run(["alert", "list"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(alert1, r.stdout)
            self.assertIn(alert2, r.stdout)
            # severity-ordered: critical before high.
            self.assertLess(r.stdout.index(alert2), r.stdout.index(alert1),
                            "critical must sort before high")

            r = _run(["alert", "resolve", alert1, "--project-id", "proj1",
                      "--reason", "fixed the queue"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("resolved_at", json.loads(r.stdout))

            r = _run(["alert", "list"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(alert1, r.stdout,
                             "a resolved alert must not appear unresolved")
            self.assertIn(alert2, r.stdout)

            r = _run(["alert", "list", "--all"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(alert1, r.stdout)
            self.assertIn("RESOLVED", r.stdout)

    def test_alert_resolve_refuses_an_unknown_alert(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["alert", "resolve", "nope", "--project-id", "proj1",
                      "--reason", "trying"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)

    def test_status_shows_unresolved_alerts_severity_ordered(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            for severity in ("info", "critical", "attention"):
                r = _run(["alert", "raise", "--project-id", "proj1",
                          "--severity", severity,
                          "--message", "m-%s" % severity]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["status", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(r.stdout.index("m-critical"),
                            r.stdout.index("m-attention"))
            self.assertLess(r.stdout.index("m-attention"),
                            r.stdout.index("m-info"))

    def test_status_next_reforecast_event_round_trips(self):
        """D-4: forecast add records next_reforecast_event; status prints
        it so the founder sees when the number is due to move."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["forecast", "add", "--project-id", "proj1",
                      "--min-duration", "two hours",
                      "--likely-duration", "three hours",
                      "--max-duration", "four hours",
                      "--input-token-range", "five k",
                      "--confidence", "medium",
                      "--next-reforecast-event",
                      "the sandbox check succeeds"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["status", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("the sandbox check succeeds", r.stdout)
            r = _run(["forecast", "show", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("the sandbox check succeeds", r.stdout)

    def test_status_omits_the_forecast_section_note_when_none_exists(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["status", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("latest forecast: (none yet)", r.stdout)

    def test_status_history_prints_last_n_attribution_events(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            # now_iso() is second precision, documented in bm_store.py's own
            # docstring as NOT microsecond-precise ("two rows written a
            # millisecond apart cannot be compared as if they were"), and
            # list_attribution's tie break for two rows in the SAME second
            # is event_id (a random uuid4 hex), not insertion order. A real
            # gap past the second boundary is what makes "the MOST RECENT
            # event" an unambiguous claim this test can assert on, rather
            # than a coin flip on two uuid hexes.
            time.sleep(1.1)
            r = _run(["task", "add", "--project-id", "proj1",
                      "--task-id", "task-x", "--title", "Do a thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["status", "--project-id", "proj1", "--history", "1"],
                     root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("attribution history (showing 1):", r.stdout)
            self.assertIn("task.created", r.stdout)
            self.assertNotIn("project.upserted", r.stdout,
                             "--history 1 must show only the MOST RECENT "
                             "event, task.created, not the older "
                             "project.upserted")

            r = _run(["status", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("attribution history", r.stdout,
                             "omitting --history must omit the section "
                             "entirely")


class TestNumbersTraceToRows(unittest.TestCase):
    """D-3, THE GATE TEST (Loop 5 design, docs/superpowers/specs/
    2026-08-01-loop4-close-and-loop5-design.md): 'every displayed number
    traces to a row and its evidence.' This test drives one full project
    (start, three tasks through three different states, a forecast, two
    alerts, a review, and a --partial deliver), captures the stdout of
    status, next, forecast show, and alert list plus the DELIVERY-
    PACKET.md file, extracts EVERY digit sequence from that text, and
    asserts each one equals a value fetched INDEPENDENTLY through the D-2
    accessors in THIS test (never read back out of the CLI's own --json,
    which would only prove the CLI agrees with itself). A hardcoded or
    derived-without-a-row number fails this test.

    IDENTIFIERS AND TIMESTAMPS, DOCUMENTED (D-3 permits excluding these
    'where infeasible'; this test does not skip them wholesale, it
    resolves them exactly). Every project_id and task_id chosen below is
    spelled with letters only ('proj-alpha', 'task-one', 'task-two',
    'task-three'), and every forecast duration is spelled in words ('two
    hours', not '2 hours'), so NONE of them can inject a spurious digit
    token in the first place. The three values this project's shape
    cannot avoid generating with digits -- forecast_id, the two alert_ids,
    and the evidence_id (uuid4 hex) -- are captured exactly via --out-json
    and stripped from the captured text by exact substring before the
    digit scan, rather than declared unverifiable. Attribution timestamps
    are handled the same way: every one is fetched via list_attribution
    and stripped by its own exact string before the scan, because a clock
    reading is not a row-derived FACT this test could recompute, but it
    IS a value this test can read from the very row that produced it.

    After that stripping, every remaining digit token must be either (a)
    a COUNT this test recomputes independently via the accessors (task
    state counts, the unresolved-alert count, next's ready-candidate
    count, forecast show's prior-forecast count, --history's shown
    count), or (b) literally contained in the forecast row's own token-
    range fields (the one field in this project deliberately given digits,
    'ten k' would have been just as valid but '10k-20k' proves the
    mechanism catches CONTENT numbers, not only counts)."""

    _DIGITS_RE = re.compile(r"\d+")

    def test_every_displayed_number_traces_to_a_row(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)

            # -- start ----------------------------------------------------
            r = _run(["start", "--project-id", "proj-alpha",
                      "--name", "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)

            # -- three tasks, walked to three DIFFERENT final states -------
            for task_id, title in (("task-one", "Fix the leak"),
                                   ("task-two", "Write the docs"),
                                   ("task-three", "Talk to the customer")):
                r = _run(["task", "add", "--project-id", "proj-alpha",
                          "--task-id", task_id, "--title", title]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)

            # task-one: walked all the way to closed.
            r = _run(["task", "transition", "--task-id", "task-one",
                      "--to", "ready", "--reason", "deps clear"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "start", "--task-id", "task-one",
                      "--reason", "beginning"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "transition", "--task-id", "task-one",
                      "--to", "awaiting review", "--reason", "work done"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["review", "task-one", "--project-id", "proj-alpha",
                      "--kind", "test", "--ref", "tools/test_all.py",
                      "--reason", "checks passed"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            evidence_id = json.loads(r.stdout)["evidence_id"]
            for state, reason in (("accepted", "owner approved"),
                                  ("delivered", "handed off"),
                                  ("monitored", "watching"),
                                  ("closed", "monitoring window over")):
                r = _run(["task", "transition", "--task-id", "task-one",
                          "--to", state, "--reason", reason]
                         + list(ACTOR), root)
                self.assertEqual(r.returncode, 0, r.stderr)

            # task-two: planned -> ready, left there.
            r = _run(["task", "transition", "--task-id", "task-two",
                      "--to", "ready", "--reason", "deps clear"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)

            # task-three: left in planned.

            # -- forecast (durations in words; token ranges carry the
            # deliberate digits this test proves trace to the row) -------
            r = _run(["forecast", "add", "--project-id", "proj-alpha",
                      "--min-duration", "two hours",
                      "--likely-duration", "three hours",
                      "--max-duration", "four hours",
                      "--input-token-range", "10k-20k",
                      "--output-token-range", "5k-15k",
                      "--confidence", "medium",
                      "--basis", "reused the existing module",
                      "--next-reforecast-event",
                      "the sandbox check succeeds"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            forecast_id = json.loads(r.stdout)["forecast_id"]

            # -- two alerts, left unresolved --------------------------------
            r = _run(["alert", "raise", "--project-id", "proj-alpha",
                      "--severity", "high", "--message", "queue backing up"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            alert_id_1 = json.loads(r.stdout)["alert_id"]
            r = _run(["alert", "raise", "--project-id", "proj-alpha",
                      "--severity", "critical", "--message", "db offline",
                      "--requires-human"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            alert_id_2 = json.loads(r.stdout)["alert_id"]

            # -- deliver, partial (task-two/task-three are not closed) -----
            # --raw: this gate traces every displayed NUMBER back to an
            # independently fetched row count, comparing against raw=True
            # store reads below; DELIVERY-PACKET.md is now redacted by
            # default (it is an export, see render_delivery_packet's own
            # docstring), and a withheld "[WITHHELD: N chars]" marker's own
            # char count is a number with no row behind it, which is a
            # different property than this gate checks. --raw keeps this
            # test's own number-tracing purpose unchanged.
            r = _run(["deliver", "--project-id", "proj-alpha", "--partial",
                      "--raw"], root)
            self.assertEqual(r.returncode, 0, r.stderr)

            # -- capture every surface the gate covers ----------------------
            r_status = _run(["status", "--project-id", "proj-alpha",
                             "--history", "3"], root)
            self.assertEqual(r_status.returncode, 0, r_status.stderr)
            r_next = _run(["next", "--project-id", "proj-alpha"], root)
            self.assertEqual(r_next.returncode, 0, r_next.stderr)
            r_forecast = _run(["forecast", "show", "--project-id",
                               "proj-alpha"], root)
            self.assertEqual(r_forecast.returncode, 0, r_forecast.stderr)
            r_alerts = _run(["alert", "list"], root)
            self.assertEqual(r_alerts.returncode, 0, r_alerts.stderr)
            packet_path = os.path.join(root, "DELIVERY-PACKET.md")
            with io.open(packet_path, encoding="utf-8") as fh:
                packet_text = fh.read()

            # -- fetch every number INDEPENDENTLY, through the same D-2
            # accessors bm_project.py itself uses, never through the CLI's
            # own JSON ------------------------------------------------------
            store = bs.Store(root, create=False)
            try:
                planned_count = len(store.list_tasks(
                    "proj-alpha", status="planned", raw=True))
                ready_count = len(store.list_tasks(
                    "proj-alpha", status="ready", raw=True))
                closed_count = len(store.list_tasks(
                    "proj-alpha", status="closed", raw=True))
                unresolved_alerts = store.list_alerts(resolved=False,
                                                       raw=True)
                all_forecasts = store.list_forecasts("proj-alpha", raw=True)
                latest_forecast = store.latest_forecast("proj-alpha",
                                                         raw=True)
                all_attribution = store.list_attribution(
                    "proj-alpha", limit=50, raw=True)
                history_3 = store.list_attribution(
                    "proj-alpha", limit=3, raw=True)
            finally:
                store.close()

            self.assertEqual(planned_count, 1)
            self.assertEqual(ready_count, 1)
            self.assertEqual(closed_count, 1)
            self.assertEqual(len(unresolved_alerts), 2)
            self.assertEqual(len(all_forecasts), 1)
            self.assertIsNotNone(latest_forecast)
            self.assertEqual(len(history_3), 3)
            prior_forecast_count = len(all_forecasts) - 1  # == 0

            # -- structured checks: specific numbers against specific,
            # independently-fetched expectations, not just "it appears
            # somewhere" -----------------------------------------------------
            self.assertIn("  planned: %d" % planned_count, r_status.stdout)
            self.assertIn("  ready: %d" % ready_count, r_status.stdout)
            self.assertIn("closed: %d" % closed_count, r_status.stdout)
            self.assertIn(
                "unresolved alerts (store-wide; alerts carry no "
                "project_id): %d" % len(unresolved_alerts),
                r_status.stdout)
            self.assertIn("attribution history (showing %d):" % len(history_3),
                          r_status.stdout)
            self.assertIn(
                "WHY: %d candidate(s)" % ready_count, r_next.stdout,
                "next's candidate count must equal the independently "
                "fetched ready-task count")
            self.assertIn("prior forecasts: %d" % prior_forecast_count,
                          r_forecast.stdout)

            # -- the generic sweep: EVERY digit sequence left in the
            # captured text, after stripping the known ids and timestamps
            # this project generated, must trace to one of the counts
            # above or to the forecast row's own token-range content -----
            known_ids = [forecast_id, alert_id_1, alert_id_2, evidence_id]
            known_timestamps = [a.get("timestamp") for a in all_attribution]
            expected_counts = {
                str(planned_count), str(ready_count), str(closed_count),
                str(len(unresolved_alerts)), str(prior_forecast_count),
                str(len(history_3)),
            }
            content_digits = set(self._DIGITS_RE.findall(
                " ".join([
                    latest_forecast.get("input_token_range") or "",
                    latest_forecast.get("output_token_range") or "",
                    latest_forecast.get("effective_total_token_range")
                    or "",
                ])))
            allowed = expected_counts | content_digits

            surfaces = {
                "status": r_status.stdout,
                "next": r_next.stdout,
                "forecast show": r_forecast.stdout,
                "alert list": r_alerts.stdout,
                "DELIVERY-PACKET.md": packet_text,
            }
            for label, text in surfaces.items():
                stripped = text
                for token in known_ids + known_timestamps:
                    if token:
                        stripped = stripped.replace(token, "")
                for digit_token in self._DIGITS_RE.findall(stripped):
                    self.assertIn(
                        digit_token, allowed,
                        "%s displays the number %r with no row behind it "
                        "(after stripping known ids/timestamps): full "
                        "text was %r" % (label, digit_token, text))


class TestExportAndPurge(unittest.TestCase):
    """WP-H (D-3, docs/superpowers/specs/2026-08-01-loop6-security-closure-
    design.md): export and purge, driven as real subprocesses against a
    fresh store, matching every other class in this file."""

    def _seeded_project(self, root):
        """proj1 with two tasks (task2 depends_on task1, so one
        dependencies row), one forecast, one open alert, and two evidence
        rows: one filed on task1 through `review` (the only CLI door onto
        evidence), one filed on the PROJECT itself directly through the
        store, because bm_project.py has no subcommand that can (review is
        task-only by construction; see its own docstring). Returns the ids
        a test needs to check export/purge against, independent of the
        CLI's own JSON."""
        _init(root)
        r = _run(["start", "--project-id", "proj1", "--name", "Acme Rescue",
                  "--goal", "Ship the thing"] + list(ACTOR)
                 + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "add", "--project-id", "proj1", "--task-id",
                  "task1", "--title", "Fix the leaky pipe"] + list(ACTOR)
                 + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "add", "--project-id", "proj1", "--task-id",
                  "task2", "--title", "Then paint the wall",
                  "--depends-on", "task1"] + list(ACTOR)
                 + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["forecast", "add", "--project-id", "proj1",
                  "--min-duration", "two hours",
                  "--likely-duration", "three hours",
                  "--max-duration", "four hours",
                  "--input-token-range", "five k",
                  "--confidence", "medium"] + list(ACTOR)
                 + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["alert", "raise", "--project-id", "proj1", "--severity",
                  "high", "--message", "careful with the wiring"]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        alert_id = json.loads(r.stdout)["alert_id"]
        for args in (
                ["task", "transition", "--task-id", "task1", "--to",
                 "ready", "--reason", "deps clear"],
                ["task", "start", "--task-id", "task1", "--reason",
                 "beginning"],
                ["task", "transition", "--task-id", "task1", "--to",
                 "awaiting review", "--reason", "ready for review"]):
            r = _run(args + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["review", "task1", "--project-id", "proj1", "--reason",
                  "looks good", "--note", "checked the pipe"]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        task_evidence_id = json.loads(r.stdout)["evidence_id"]
        store = bs.Store(root, create=False)
        try:
            project_evidence_id = store.add_evidence(
                {"evidence_id": "ev-proj", "subject_type": "project",
                 "subject_id": "proj1", "kind": "note",
                 "note": "founder sign-off", "created_at": bs.now_iso()},
                "proj1", {"actor_type": "human", "actor_name": "khalil"})
        finally:
            store.close()
        return {"alert_id": alert_id, "task_evidence_id": task_evidence_id,
                "project_evidence_id": project_evidence_id}

    def test_export_redacted_by_default_raw_reveals_round_trips_every_table(
            self):
        with tempfile.TemporaryDirectory() as root:
            seeded = self._seeded_project(root)

            redacted_path = os.path.join(root, "redacted-export.json")
            r = _run(["export", "--project-id", "proj1", "--out",
                      redacted_path], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(redacted_path, r.stdout)
            with io.open(redacted_path, encoding="utf-8") as fh:
                redacted = json.load(fh)

            # Every table the design names (D-3: "every row the store
            # holds for that project") round-trips as its own section.
            self.assertEqual(
                set(redacted) - {"project_id", "exported_at", "raw"},
                {"project", "tasks", "dependencies", "forecasts", "alerts",
                 "evidence", "attribution"})
            self.assertFalse(redacted["raw"])
            blob = json.dumps(redacted)
            self.assertNotIn("Acme Rescue", blob)
            self.assertNotIn("Fix the leaky pipe", blob)
            self.assertNotIn("founder sign-off", blob)
            self.assertTrue(
                redacted["project"]["name"].startswith("[WITHHELD"),
                redacted["project"])
            # Identifiers (never founder prose) survive redaction, which is
            # what "round-trips every table" actually checks: the SHAPE and
            # the row COUNT for each table are intact even though the
            # prose inside each row is withheld.
            self.assertEqual(redacted["project"]["project_id"], "proj1")
            self.assertEqual(
                sorted(t["task_id"] for t in redacted["tasks"]),
                ["task1", "task2"])
            self.assertEqual(
                redacted["dependencies"],
                [{"task_id": "task2", "depends_on_task_id": "task1"}])
            self.assertEqual(len(redacted["forecasts"]), 1)
            self.assertEqual(
                [a["alert_id"] for a in redacted["alerts"]],
                [seeded["alert_id"]])
            self.assertEqual(
                sorted(e["evidence_id"] for e in redacted["evidence"]),
                sorted([seeded["task_evidence_id"],
                        seeded["project_evidence_id"]]))
            self.assertGreaterEqual(len(redacted["attribution"]), 8)

            raw_path = os.path.join(root, "raw-export.json")
            r = _run(["export", "--project-id", "proj1", "--raw", "--out",
                      raw_path], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(raw_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertTrue(raw["raw"])
            self.assertEqual(raw["project"]["name"], "Acme Rescue")
            self.assertEqual(
                sorted(t["title"] for t in raw["tasks"]),
                ["Fix the leaky pipe", "Then paint the wall"])
            self.assertIn(
                "careful with the wiring",
                [a["message"] for a in raw["alerts"]])
            self.assertIn(
                "founder sign-off",
                [e.get("note") for e in raw["evidence"]])

    def test_export_default_out_path_is_project_scoped_in_root(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            r = _run(["export", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            # realpath on the expected side, because the CLI prints the path its
            # own root resolver produced and that resolver canonicalizes. On
            # Windows the two spellings of one directory differ visibly:
            # tempfile hands back the 8.3 short form (C:\Users\RUNNER~1\...)
            # while the resolver returns the long form (C:\Users\runneradmin\...),
            # so the unresolved comparison failed on both Windows legs of CI
            # while passing everywhere else. Same directory, two names.
            expected = os.path.join(os.path.realpath(root), "EXPORT-proj1.json")
            self.assertIn(expected, r.stdout)
            self.assertTrue(os.path.isfile(expected))

    def test_export_refuses_unknown_project(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["export", "--project-id", "nope"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no project", r.stderr)
            self.assertEqual(
                [f for f in os.listdir(root) if f.endswith(".json")], [],
                "a refused export must write no file")

    def test_purge_without_confirm_refuses_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            before = _raw_dump(root)
            r = _run(["purge", "--project-id", "proj1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("confirm", r.stderr)
            self.assertEqual(_raw_dump(root), before)

    def test_purge_dry_run_reports_the_real_counts_and_removes_nothing(self):
        """V3 Final B1. The whole point of the flag is that its counts are
        the REAL purge's counts, so this asserts both halves at once: the
        store is byte-for-byte unchanged afterwards, AND the numbers match
        what the real purge on the same fixture reports. A preview that
        removed nothing but reported the wrong thing would pass a weaker
        version of this test and be worse than useless, because a founder
        reads it precisely when they are about to do something they cannot
        undo."""
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            before = _raw_dump(root)
            r = _run(["purge", "--project-id", "proj1", "--dry-run"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(
                _raw_dump(root), before,
                "a dry run must leave every row, including the attribution "
                "trail, exactly as it was")
            # Now really purge the SAME fixture shape and compare. This is
            # the assertion that a reimplemented preview would fail.
            with tempfile.TemporaryDirectory() as root2:
                self._seeded_project(root2)
                r2 = _run(["purge", "--project-id", "proj1", "--confirm",
                           "proj1"] + list(ACTOR) + ["--out-json"], root2)
                self.assertEqual(r2.returncode, 0, r2.stderr)
                self.assertEqual(
                    payload["removed"], json.loads(r2.stdout)["removed"],
                    "the dry run's counts must equal the real purge's own")

    def test_purge_dry_run_needs_no_confirm_but_still_needs_a_project(self):
        """The confirmation token guards an irreversible deletion, and a dry
        run is not one, so --dry-run alone is enough. --project-id stays
        required, because a preview of 'some project' is not a preview."""
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            before = _raw_dump(root)
            r = _run(["purge", "--dry-run"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("project-id", r.stderr)
            self.assertEqual(_raw_dump(root), before)

    def test_purge_dry_run_prints_the_dry_run_prefix_and_no_past_tense(self):
        """Mirrors scripts/uninstall.py's own '[dry-run] ' prefix. The past
        tense is the danger: 'removed: 2 task(s)' on a run that removed
        nothing is the sentence a founder would act on."""
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            r = _run(["purge", "--project-id", "proj1", "--dry-run"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[dry-run] ", r.stdout)
            self.assertIn("would remove", r.stdout)
            self.assertIn("nothing was removed", r.stdout)
            self.assertNotIn("purged project proj1", r.stdout)

    def test_purge_wrong_confirm_refuses_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            before = _raw_dump(root)
            r = _run(["purge", "--project-id", "proj1", "--confirm", "nope"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("refused", r.stderr)
            self.assertEqual(_raw_dump(root), before,
                              "a wrong --confirm must change nothing")

    def test_purge_with_confirm_removes_rows_attribution_trail_survives(
            self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            before = _raw_dump(root)
            prior_attribution = len(
                [e for e in before["attribution"]
                 if e["project_id"] == "proj1"])
            r = _run(["purge", "--project-id", "proj1", "--confirm",
                      "proj1"] + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            removed = json.loads(r.stdout)["removed"]
            self.assertEqual(
                removed, {"dependencies": 1, "evidence": 2, "alerts": 1,
                          "forecasts": 1, "tasks": 2, "projects": 1,
                          "cross_project_edges_removed": [],
                          "alerts_skipped": [],
                          # L02 (schema 14) put six autonomy tables under the
                          # same purge; this fixture seeds none, so each is 0,
                          # and the purge reports them anyway rather than
                          # hiding a table it touched.
                          "autonomy_contracts": 0, "autonomy_spend": 0,
                          "autonomy_assumptions": 0,
                          "autonomy_interruptions": 0,
                          "autonomy_human_steps": 0,
                          "autonomy_checkpoints": 0,
                          # L03 (schema 15) put the three controller tables
                          # under the same purge, children first for the FK
                          # order (dispatches, units, runs). Same rule as the
                          # six above: this fixture seeds none, so each is 0,
                          # and a table the purge touches is reported rather
                          # than hidden. A schema that adds a purged table
                          # without adding its key here turns this red, which
                          # is the point of pinning the WHOLE dict.
                          "controller_dispatches": 0,
                          "controller_units": 0,
                          "controller_runs": 0,
                          # L04 (schema 16) put the insight ledger and the
                          # briefing timeline under the same purge. Same
                          # rule and same reason as the block above: this
                          # fixture seeds neither, so each is 0, and these
                          # two keys had to be added here precisely because
                          # the pin did its designed job and turned red the
                          # moment purge_project started removing them.
                          "insights": 0,
                          "briefings": 0,
                          # TK10 (schema 20) put capability_receipts under
                          # the same purge. Same rule as every block above:
                          # this fixture seeds none, so it is 0, and this
                          # key turned the pin red the moment purge started
                          # removing the table, which is the pin's job.
                          "capability_receipts": 0,
                          # SBE10 (2026-08-16) put the four sentinel tables
                          # under the same purge, and they are the reason
                          # the finding mattered: they were the ones left
                          # holding raw founder prose after a purge that
                          # reported success. The pin did its job again.
                          "sentinel_knowledge": 0,
                          "sentinel_procedural": 0,
                          "sentinel_status": 0,
                          "sentinel_interventions": 0,
                          # L05 (schema 17) put the generated views under
                          # the same purge. Same rule and same reason as
                          # the three blocks above: this fixture seeds
                          # none, so it is 0, and this key had to be added
                          # here precisely because the pin did its designed
                          # job and turned red the moment purge_project
                          # started removing them. The non-zero case is
                          # TestViewPurgeLeavesNoOrphans in
                          # tools/test_bm_store.py.
                          "views": 0})

            after = _raw_dump(root)
            self.assertEqual(
                [p for p in after["projects"] if p["project_id"] == "proj1"],
                [])
            self.assertEqual(
                [t for t in after["tasks"] if t["project_id"] == "proj1"],
                [])
            self.assertEqual(after["dependencies"], [])
            self.assertEqual(after["alerts"], [])
            self.assertEqual(after["evidence"], [])
            # The attribution trail SURVIVES: every prior event this
            # project accumulated, plus exactly one new row naming the
            # purge, never zero rows left behind claiming nothing happened.
            proj_attribution = [
                e for e in after["attribution"]
                if e["project_id"] == "proj1"]
            self.assertEqual(len(proj_attribution), prior_attribution + 1)
            purge_events = [e for e in proj_attribution
                            if e["event_type"] == "project.purged"]
            self.assertEqual(len(purge_events), 1)

    def test_purge_text_output_names_what_it_removed_and_kept(self):
        with tempfile.TemporaryDirectory() as root:
            self._seeded_project(root)
            r = _run(["purge", "--project-id", "proj1", "--confirm",
                      "proj1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("purged project proj1", r.stdout)
            self.assertIn("removed:", r.stdout)
            self.assertIn("kept:", r.stdout)
            self.assertIn("attribution trail", r.stdout)
            self.assertIn("vault", r.stdout)

    def test_purge_names_cross_project_dependency_fallout_separately(self):
        """A6 (loop6 refuter finding): a task in ANOTHER project depending
        on a task in the purged one is real fallout, but it must be named
        in its own plain sentence, never folded into the purged project's
        own "removed:" counts."""
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "proj1", "--name",
                      "Acme Rescue", "--goal", "Ship the thing"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["start", "--project-id", "proj2", "--name",
                      "Beta Rescue", "--goal", "Ship the other thing",
                      "--allow-second"] + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "add", "--project-id", "proj1", "--task-id",
                      "task1", "--title", "Fix the leaky pipe"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["task", "add", "--project-id", "proj2", "--task-id",
                      "task4", "--title", "Depends on proj1's work",
                      "--depends-on", "task1"] + list(ACTOR)
                     + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)

            r = _run(["purge", "--project-id", "proj1", "--confirm",
                      "proj1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(
                "prerequisite link(s) in other projects also had to go",
                r.stdout)
            self.assertIn("task4", r.stdout)
            # proj1's own "removed:" line must not claim this edge as its
            # own dependency count: proj1's task1 depended on nothing.
            self.assertIn("0 dependency row(s)", r.stdout)

            store = bs.Store(root, create=False)
            try:
                task4 = store.get_task("task4", raw=True)
            finally:
                store.close()
            self.assertEqual(task4["depends_on"], [])


class TestStartScaffoldsTheProgressPage(unittest.TestCase):
    """The founder's 2026-08-09 ask, made mechanical: every project started
    with BrotherMode begins with the progress page, in the canonical design,
    carrying the tick contract. Before this, the template sat in
    project-template/PROGRESS.html and NOTHING installed it: a standard that
    exists only as a file nobody copies is a hope, not a standard, the same
    prose-not-control defect the 8 August postmortem is about."""

    def _started(self, root, project_id="proj1", name="Acme Rescue",
                 extra=(), extra_env=None):
        _init(root)
        r = _run(["start", "--project-id", project_id, "--name", name]
                 + list(extra) + list(ACTOR), root, extra_env=extra_env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_start_installs_the_progress_page(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            page = os.path.join(root, "PROGRESS.html")
            self.assertTrue(os.path.isfile(page),
                            "start must scaffold PROGRESS.html beside "
                            "CANVAS.md, or the page standard is prose")
            text = io.open(page, encoding="utf-8").read()
            self.assertIn("tick", text.lower(),
                          "the scaffolded page must carry the tick contract")

    def test_start_never_overwrites_an_existing_page(self):
        """The page is the project's own living document from the first
        refresh on. A re-run of start (an update) must not reset it to the
        template: that would destroy the very history the page exists to
        keep, and the never-lose-work law outranks scaffolding."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            page = os.path.join(root, "PROGRESS.html")
            with io.open(page, "w", encoding="utf-8") as fh:
                fh.write("the founder's own refreshed page")
            self._started(root)  # same project id: an update, not a reset
            self.assertEqual(
                io.open(page, encoding="utf-8").read(),
                "the founder's own refreshed page",
                "re-running start clobbered the live progress page")

    def test_a_missing_template_warns_and_never_fails_the_start(self):
        """A pip install ships the tools without project-template/. Losing
        the scaffold there is a stated limit; losing the whole start
        command over it would be a regression. stderr says what was
        skipped, because a silent skip is the other defect class."""
        with tempfile.TemporaryDirectory() as root:
            r = self._started(
                root, extra_env={"BROTHERMODE_PROGRESS_TEMPLATE":
                                 os.path.join(root, "no-such-template.html")})
            self.assertFalse(
                os.path.isfile(os.path.join(root, "PROGRESS.html")))
            self.assertIn("progress page", r.stderr.lower(),
                          "skipping the scaffold must say so on stderr")


    def test_a_dangling_symlink_at_the_page_name_is_not_written_through(self):
        """PR 38 review finding: os.path.exists is False for a dangling
        symlink, so the create-only guard passed and the open("w") wrote
        THROUGH the link, creating a file at wherever it pointed. The write
        must refuse the occupied name instead (create-exclusive), warn, and
        leave the start healthy."""
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "elsewhere", "written-through.html")
            os.makedirs(os.path.dirname(target))
            os.symlink(target, os.path.join(root, "PROGRESS.html"))
            r = self._started(root)
            self.assertFalse(
                os.path.exists(target),
                "the scaffold wrote through a dangling symlink")
            self.assertIn("progress page", (r.stderr or "").lower(),
                          "refusing the occupied name must say so on stderr")


class TestOutcomeContractStartFlags(unittest.TestCase):
    """R1.1 (docs/plan/LONG-RANGE-PLAN-2026-08-11.md, outcome contract
    columns): --kill-criteria and --non-goals at `start` time, exercised
    end to end as a real subprocess exactly like every other CSV-list
    start flag (--scope-in, --risks, ...) is tested elsewhere in this
    file. Row-level verification goes through _raw_dump, the same escape
    hatch every other test here uses rather than SQL of its own."""

    def test_kill_criteria_and_non_goals_land_in_the_stored_row(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "proj1", "--name", "Acme",
                      "--kill-criteria", "budget doubles,sponsor leaves",
                      "--non-goals", "mobile app,billing"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            row = [p for p in raw["projects"]
                   if p["project_id"] == "proj1"][0]
            self.assertEqual(json.loads(row["kill_criteria"]),
                             ["budget doubles", "sponsor leaves"])
            self.assertEqual(json.loads(row["non_goals"]),
                             ["mobile app", "billing"])
            # risks is untouched by this flag pair: still its own default.
            self.assertEqual(json.loads(row["risks"]), [])

    def test_a_start_without_the_flags_defaults_both_to_an_empty_list(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["start", "--project-id", "proj1", "--name", "Acme"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            row = [p for p in raw["projects"]
                   if p["project_id"] == "proj1"][0]
            self.assertEqual(json.loads(row["kill_criteria"]), [])
            self.assertEqual(json.loads(row["non_goals"]), [])

    def test_the_usage_line_names_both_flags(self):
        """cmd_start's own usage string is what a founder sees on a
        refused start; a flag that works but is never printed is a flag
        nobody discovers."""
        with tempfile.TemporaryDirectory() as root:
            r = _run(["start"], root)
            self.assertIn("--kill-criteria", r.stderr)
            self.assertIn("--non-goals", r.stderr)


class TestCapabilityReceipts(unittest.TestCase):
    """TK11 (docs/plan/QUEUE.json): `receipt add` and `receipt list`, the
    CLI surface over Store.add_capability_receipt and
    list_capability_receipts (F4, schema 20). A receipt can be filed and
    listed from the command line without touching the store directly."""

    def _started(self, root, project_id="proj1", name="Acme Rescue"):
        _init(root)
        r = _run(["start", "--project-id", project_id, "--name", name]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_receipt_list_on_a_project_with_none_states_that_plainly(self):
        """House receipt discipline: no receipt is NO-DATA; an empty list
        says so, never silence."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["receipt", "list", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no capability receipts recorded", r.stdout)

    def test_receipt_add_and_list_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--capability-name", "grep-search",
                      "--capability-version", "1.0",
                      "--executor-identity", "claude-sonnet",
                      "--task-description", "search the repo for TODO",
                      "--inputs", "pattern=TODO,path=.",
                      "--permissions-declared", "read",
                      "--claimed-output", "17 matches found",
                      "--changed-artifacts", "",
                      "--verification-state", "verified",
                      "--verification-evidence", "rerun matched 17 lines",
                      "--omissions", "binary files skipped"]
                     + list(ACTOR) + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            receipt_id = json.loads(r.stdout)["receipt_id"]
            self.assertTrue(receipt_id)

            raw = _raw_dump(root)
            self.assertEqual(len(raw["capability_receipts"]), 1)
            row = raw["capability_receipts"][0]
            self.assertEqual(row["receipt_id"], receipt_id)
            self.assertEqual(row["capability_name"], "grep-search")
            self.assertEqual(row["verification_state"], "verified")
            self.assertEqual(json.loads(row["inputs"]),
                             ["pattern=TODO", "path=."])
            self.assertEqual(json.loads(row["omissions"]),
                             ["binary files skipped"])

            r = _run(["receipt", "list", "--project-id", "proj1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(receipt_id, r.stdout)
            self.assertIn("grep-search", r.stdout)
            self.assertIn("verified", r.stdout)

            r = _run(["receipt", "list", "--project-id", "proj1", "--json"],
                     root)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(len(out["receipts"]), 1)
            self.assertEqual(out["receipts"][0]["receipt_id"], receipt_id)

    def test_receipt_add_records_no_data_as_a_first_class_state(self):
        """T5's own words: 'no_data is a first-class value, not a null'.
        A capability that returned prose with nothing runnable gets a
        receipt that says so honestly, and this must not be refused."""
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--capability-name", "prose-only-tool",
                      "--task-description", "summarize the design doc",
                      "--verification-state", "no_data"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["capability_receipts"][0]["verification_state"],
                             "no_data")

    def test_receipt_add_refuses_an_unknown_verification_state(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--capability-name", "grep-search",
                      "--task-description", "search the repo",
                      "--verification-state", "probably-fine"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("verification_state must be one of", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["capability_receipts"], [])

    def test_receipt_add_requires_capability_name_and_task_description(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--verification-state", "verified"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--capability-name", r.stderr)

    def test_receipt_add_refuses_an_unknown_project(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["receipt", "add", "--project-id", "nosuch",
                      "--capability-name", "grep-search",
                      "--task-description", "search the repo",
                      "--verification-state", "verified"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no project", r.stderr)

    def test_receipt_list_narrows_by_task_id_and_capability_name(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            r = _run(["task", "add", "--project-id", "proj1",
                      "--title", "Search task"] + list(ACTOR)
                     + ["--out-json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            task_id = json.loads(r.stdout)["task_id"]
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--task-id", task_id,
                      "--capability-name", "grep-search",
                      "--task-description", "search the repo",
                      "--verification-state", "verified",
                      # SBE12, 2026-08-16: 'verified' now costs an executor
                      # and a piece of evidence, refused by name without
                      # them, so this call carries what the sibling test
                      # above it already carried.
                      "--executor-identity", "grep 3.11 on this machine",
                      "--verification-evidence", "rg --count returned 4"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(["receipt", "add", "--project-id", "proj1",
                      "--capability-name", "other-tool",
                      "--task-description", "unrelated work",
                      "--verification-state", "failed"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)

            r = _run(["receipt", "list", "--project-id", "proj1",
                      "--task-id", task_id], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("grep-search", r.stdout)
            self.assertNotIn("other-tool", r.stdout)

            r = _run(["receipt", "list", "--project-id", "proj1",
                      "--capability-name", "other-tool"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("other-tool", r.stdout)
            self.assertNotIn("grep-search", r.stdout)


class TestReviewCriterionId(unittest.TestCase):
    """schema 20, R1.2: `review --criterion-id` links the filed evidence to
    one entry of the reviewed task's own acceptance_checks."""

    def _started(self, root, project_id="proj1", name="Acme Rescue"):
        _init(root)
        r = _run(["start", "--project-id", project_id, "--name", name]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _task_with_checks(self, root, checks):
        payload = os.path.join(root, "task.json")
        with io.open(payload, "w", encoding="utf-8") as fh:
            json.dump({"acceptance_checks": checks}, fh)
        r = _run(["task", "add", "--project-id", "proj1",
                  "--title", "Checked task", "--json", payload]
                 + list(ACTOR) + ["--out-json"], root)
        self.assertEqual(r.returncode, 0, r.stderr)
        task_id = json.loads(r.stdout)["task_id"]
        # planned -> ready -> active: the ten-state law admits 'active'
        # only from 'ready' or 'blocked' (see cmd_task_start's own
        # docstring), so a fresh task must pass through 'ready' first.
        r = _run(["task", "transition", "--task-id", task_id,
                  "--to", "ready", "--reason", "dependencies satisfied"]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["task", "start", "--task-id", task_id,
                  "--reason", "begin"] + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)
        # active -> awaiting review: `review`'s own default destination is
        # 'verified', legal only from 'awaiting review' (LEGAL_TRANSITIONS,
        # brotherme/core/schema.py), so a task must reach that state first.
        r = _run(["task", "transition", "--task-id", task_id,
                  "--to", "awaiting review", "--reason", "work is done"]
                 + list(ACTOR), root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return task_id

    def test_review_criterion_id_links_to_a_real_acceptance_check(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            task_id = self._task_with_checks(root, ["grep returns empty"])
            cid = bs._schema().criterion_id_for_check("grep returns empty")
            r = _run(["review", task_id, "--project-id", "proj1",
                      "--criterion-id", cid, "--reason", "verified it"]
                     + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            ev = [e for e in raw["evidence"] if e["subject_id"] == task_id][0]
            self.assertEqual(ev["criterion_id"], cid)

    def test_review_refuses_a_criterion_id_matching_no_check(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            task_id = self._task_with_checks(root, ["grep returns empty"])
            r = _run(["review", task_id, "--project-id", "proj1",
                      "--criterion-id", "not-a-real-id",
                      "--reason", "verified it"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("criterion_id", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(
                [e for e in raw["evidence"] if e["subject_id"] == task_id],
                [], "a refused criterion_id must write no evidence row")

    def test_review_without_criterion_id_leaves_evidence_unlinked(self):
        with tempfile.TemporaryDirectory() as root:
            self._started(root)
            task_id = self._task_with_checks(root, ["grep returns empty"])
            r = _run(["review", task_id, "--project-id", "proj1",
                      "--reason", "verified it"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            ev = [e for e in raw["evidence"] if e["subject_id"] == task_id][0]
            self.assertEqual(ev["criterion_id"], "")


if __name__ == "__main__":
    unittest.main()
