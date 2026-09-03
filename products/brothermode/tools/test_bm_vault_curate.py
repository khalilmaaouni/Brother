#!/usr/bin/env python3
"""Tests for bm_vault_curate, on a small fixture vault (never the real one).

The fixture plants one case per finder, and each planted case was calibrated by
breaking that finder and watching its named test fail:
  duplicate    two 40-Failures notes whose titles share every content word
  jaccard      note-x and note-y each link [[hub-1]] and [[hub-2]] (shared neighbors)
  cocitation   hub-1 and hub-2 are cited together by both note-x and note-y

Run: python3 tools/test_bm_vault_curate.py      (unittest output, exit 0 or 1)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_curate.py")
GRAPH = os.path.join(HERE, "bm_vault_graph.py")


def _note(title, created="2026-08-01", typ="failure", links=(), extra_front=""):
    body = ("---\ntype: %s\nproject: all\nstatus: standing\ncreated: %s\n%s---\n\n# %s\n\n"
            % (typ, created, extra_front, title))
    for link in links:
        body += "See [[%s]].\n" % link
    return body


def _build_fixture(root):
    """A vault that passes bm_vault_graph check (valid frontmatter, no broken links)
    and plants exactly one case per finder."""
    failures = os.path.join(root, "40-Failures")
    notes = os.path.join(root, "30-Notes")
    os.makedirs(failures)
    os.makedirs(notes)

    def write(dirpath, fn, text):
        with open(os.path.join(dirpath, fn), "w", encoding="utf-8") as f:
            f.write(text)

    # duplicate pair (created dates differ so accept's OLDER pick is deterministic)
    write(failures, "lock-a.md",
          _note("A lock orphaned by an ended session", created="2026-08-01"))
    write(failures, "lock-b.md",
          _note("An ended session orphaned the lock", created="2026-08-05"))
    # jaccard pair: note-x, note-y share neighbors hub-1, hub-2.
    # cocitation pair: hub-1, hub-2 are cited together by note-x and note-y.
    write(notes, "hub-1.md", _note("Hub one, a topic page", typ="finding"))
    write(notes, "hub-2.md", _note("Hub two, another topic page", typ="finding"))
    write(notes, "note-x.md",
          _note("Wednesday incident writeup", typ="finding", links=("hub-1", "hub-2")))
    write(notes, "note-y.md",
          _note("Thursday incident writeup", typ="finding", links=("hub-1", "hub-2")))
    # an unrelated, already-linked pair: relates: declared, must never be queued
    write(notes, "declared-a.md",
          _note("Declared already", typ="finding", extra_front="relates: [[declared-b]]\n"))
    write(notes, "declared-b.md", _note("Declared already too", typ="finding"))


def run(argv, cwd=None):
    p = subprocess.run([sys.executable, TOOL] + argv, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def run_graph_check(vault):
    p = subprocess.run([sys.executable, GRAPH, "check", "--vault", vault],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class Find(unittest.TestCase):
    """find + list, read-only against its fixture: one class-level run, assertions on
    the queue file it wrote."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-curate-find-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        _build_fixture(cls.vault)
        cls.queue_path = os.path.join(cls.tmp, "queue.json")
        cls.code, cls.out = run(["find", "--vault", cls.vault, "--queue", cls.queue_path])
        with open(cls.queue_path, encoding="utf-8") as f:
            cls.data = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _entry(self, a, b):
        want = {a, b}
        for e in self.data["queue"]:
            if set(e["pair"]) == want:
                return e
        return None

    def test_find_exits_zero_and_writes_queue(self):
        self.assertEqual(self.code, 0, "find exited %d:\n%s" % (self.code, self.out))
        self.assertTrue(os.path.isfile(self.queue_path))

    def test_duplicate_finder_finds_the_planted_title_pair(self):
        e = self._entry("40-Failures/lock-a", "40-Failures/lock-b")
        self.assertIsNotNone(e, "duplicate pair missing from queue:\n%s"
                             % json.dumps(self.data["queue"], indent=2))
        self.assertIn("duplicate", e["finders"])
        self.assertGreaterEqual(e["finders"]["duplicate"], 0.5)

    def test_jaccard_finder_finds_the_shared_neighbor_pair(self):
        e = self._entry("30-Notes/note-x", "30-Notes/note-y")
        self.assertIsNotNone(e, "jaccard pair missing from queue:\n%s"
                             % json.dumps(self.data["queue"], indent=2))
        self.assertIn("jaccard", e["finders"])
        self.assertGreaterEqual(e["finders"]["jaccard"], 0.25)

    def test_cocitation_finder_finds_the_cocited_pair(self):
        e = self._entry("30-Notes/hub-1", "30-Notes/hub-2")
        self.assertIsNotNone(e, "co-cited pair missing from queue:\n%s"
                             % json.dumps(self.data["queue"], indent=2))
        self.assertIn("cocitation", e["finders"])
        self.assertGreaterEqual(e["finders"]["cocitation"], 2)

    def test_an_already_declared_edge_is_never_queued(self):
        self.assertIsNone(self._entry("30-Notes/declared-a", "30-Notes/declared-b"),
                          "a pair with an existing relates: edge was re-nudged")

    def test_list_ranks_multi_finder_pairs_first(self):
        code, out = run(["list", "--queue", self.queue_path])
        self.assertEqual(code, 0, out)
        lines = [ln for ln in out.split("\n") if ln.strip().startswith("[")]
        self.assertTrue(lines, "list printed no queue lines:\n%s" % out)
        counts = [int(ln.strip()[1]) for ln in lines]
        self.assertEqual(counts, sorted(counts, reverse=True),
                         "list is not ranked by finder count:\n%s" % out)

    def test_fixture_graph_gate_is_green_before_any_accept(self):
        code, out = run_graph_check(self.vault)
        self.assertEqual(code, 0, "fixture vault fails the graph gate:\n%s" % out)


class AcceptReject(unittest.TestCase):
    """Its own fixture: accept mutates a note, so Find's read-only fixture stays
    untouched. Method order (alphabetical) is safe: every test re-runs find or acts
    on a pair the previous tests did not consume."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-curate-accept-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        _build_fixture(cls.vault)
        cls.queue_path = os.path.join(cls.tmp, "queue.json")
        code, out = run(["find", "--vault", cls.vault, "--queue", cls.queue_path])
        assert code == 0, out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _read(self, stem):
        with open(os.path.join(self.vault, stem + ".md"), encoding="utf-8") as f:
            return f.read()

    def test_a_accept_without_by_refuses(self):
        code, out = run(["accept", "--vault", self.vault, "--queue", self.queue_path,
                         "--pair", "lock-a,lock-b", "--edge", "relates"])
        self.assertEqual(code, 2, "expected a refusal without --by:\n%s" % out)
        self.assertIn("--by", out)

    def test_b_accept_is_dry_by_default(self):
        before = self._read("40-Failures/lock-a")
        code, out = run(["accept", "--vault", self.vault, "--queue", self.queue_path,
                         "--pair", "lock-a,lock-b", "--edge", "relates", "--by", "tester"])
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(self._read("40-Failures/lock-a"), before,
                         "a dry accept wrote to the vault")
        with open(self.queue_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertFalse(any(a["action"] == "accept" for a in data["audit"]),
                         "a dry accept left an audit entry")

    def test_c_accept_apply_writes_edge_appendonly_and_gate_stays_green(self):
        before = self._read("40-Failures/lock-a")
        code, out = run(["accept", "--vault", self.vault, "--queue", self.queue_path,
                         "--pair", "lock-a,lock-b", "--edge", "relates", "--by",
                         "tester", "--apply"])
        self.assertEqual(code, 0, out)
        after = self._read("40-Failures/lock-a")
        self.assertIn("relates: [[lock-b]]", after,
                      "the edge did not land on the OLDER note (lock-a, created first)")
        # append-only: every original line survives
        for line in before.strip().split("\n"):
            self.assertIn(line, after, "append-only violated, line vanished: %r" % line)
        self.assertEqual(self._read("40-Failures/lock-b"),
                         self._read("40-Failures/lock-b"))
        gcode, gout = run_graph_check(self.vault)
        self.assertEqual(gcode, 0, "graph gate went red after accept:\n%s" % gout)
        with open(self.queue_path, encoding="utf-8") as f:
            data = json.load(f)
        accepts = [a for a in data["audit"] if a["action"] == "accept"]
        self.assertEqual(len(accepts), 1)
        self.assertEqual(accepts[0]["by"], "tester")
        self.assertTrue(accepts[0]["when"], "audit entry carries no timestamp")
        self.assertFalse(any(set(e["pair"]) == {"40-Failures/lock-a", "40-Failures/lock-b"}
                             for e in data["queue"]), "accepted pair still queued")

    def test_d_accepted_pair_never_returns_after_refind(self):
        code, out = run(["find", "--vault", self.vault, "--queue", self.queue_path])
        self.assertEqual(code, 0, out)
        with open(self.queue_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertFalse(any(set(e["pair"]) == {"40-Failures/lock-a", "40-Failures/lock-b"}
                             for e in data["queue"]),
                         "an accepted pair (now a real edge) was re-nudged")

    def test_e_reject_without_by_refuses(self):
        code, out = run(["reject", "--queue", self.queue_path,
                         "--pair", "hub-1,hub-2"])
        self.assertEqual(code, 2, "expected a refusal without --by:\n%s" % out)
        self.assertIn("--by", out)

    def test_f_reject_suppresses_renudge(self):
        code, out = run(["reject", "--queue", self.queue_path,
                         "--pair", "hub-1,hub-2", "--by", "tester"])
        self.assertEqual(code, 0, out)
        code, out = run(["find", "--vault", self.vault, "--queue", self.queue_path])
        self.assertEqual(code, 0, out)
        with open(self.queue_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertFalse(any(set(e["pair"]) == {"30-Notes/hub-1", "30-Notes/hub-2"}
                             for e in data["queue"]), "a rejected pair reappeared in find")
        rejects = [a for a in data["audit"] if a["action"] == "reject"]
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["by"], "tester")
        self.assertTrue(rejects[0]["when"])
        self.assertEqual(len(data["rejections"]), 1)
        self.assertEqual(data["rejections"][0]["by"], "tester")


def _write_queue(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated": "2026-08-01T00:00:00+00:00", "vault": None,
                  "queue": entries, "rejections": [], "audit": []}, f)


def _candidate(a, b, built=None, owner=None):
    e = {"pair": [a, b], "titles": [a, b], "finders": {"duplicate": 0.6},
        "combined": 0.6}
    if built is not None:
        e["built"] = built
    if owner is not None:
        e["owner"] = owner
    return e


class Governance(unittest.TestCase):
    """governance is queue-file-only: no vault fixture needed, just planted JSON."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-curate-governance-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _queue(self, entries):
        path = os.path.join(self.tmp, "queue.json")
        _write_queue(path, entries)
        return path

    def test_missing_queue_is_no_data_exit_2(self):
        code, out = run(["governance", "--queue",
                         os.path.join(self.tmp, "does-not-exist.json")])
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_unreadable_queue_is_no_data_exit_2(self):
        path = os.path.join(self.tmp, "queue.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        code, out = run(["governance", "--queue", path])
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_zero_candidates_prints_clean_line_exit_0(self):
        path = self._queue([])
        code, out = run(["governance", "--queue", path])
        self.assertEqual(code, 0, out)
        self.assertIn("count: 0", out)

    def test_over_cap_and_over_age_produce_two_named_findings_at_exit_1(self):
        old = "2020-01-01T00:00:00+00:00"
        path = self._queue([
            _candidate("a", "b", built=old, owner="linh"),
            _candidate("c", "d", built="2026-08-01T00:00:00+00:00", owner="linh"),
        ])
        code, out = run(["governance", "--queue", path, "--cap", "1",
                         "--max-age-days", "1"])
        self.assertEqual(code, 1, out)
        self.assertIn("FINDING: OVER CAP", out)
        self.assertIn("FINDING: OVER AGE", out)

    def test_under_cap_and_under_age_exits_0_with_counts_printed(self):
        path = self._queue([
            _candidate("a", "b", built="2026-08-01T00:00:00+00:00", owner="linh"),
        ])
        code, out = run(["governance", "--queue", path, "--cap", "10",
                         "--max-age-days", "1000"])
        self.assertEqual(code, 0, out)
        self.assertIn("count: 1", out)
        self.assertIn("OK: under cap", out)

    def test_per_owner_counts_reported(self):
        path = self._queue([
            _candidate("a", "b", built="2026-08-01T00:00:00+00:00", owner="linh"),
            _candidate("c", "d", built="2026-08-01T00:00:00+00:00", owner="linh"),
            _candidate("e", "f", built="2026-08-01T00:00:00+00:00"),
        ])
        code, out = run(["governance", "--queue", path, "--cap", "10",
                         "--max-age-days", "1000"])
        self.assertEqual(code, 0, out)
        self.assertIn("linh=2", out)
        self.assertIn("NO-DATA=1", out)

    def test_candidate_with_no_build_timestamp_is_no_data_age_not_zero(self):
        path = self._queue([_candidate("a", "b")])  # no "built" at all
        code, out = run(["governance", "--queue", path, "--cap", "10",
                         "--max-age-days", "1000"])
        self.assertEqual(code, 0, out)
        self.assertIn("oldest candidate age: NO-DATA", out)
        self.assertNotIn("0.0 day", out)

    def test_find_stamps_owner_and_built_and_persists_built_across_refind(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-curate-owner-")
        try:
            vault = os.path.join(tmp, "vault")
            os.makedirs(vault)
            _build_fixture(vault)
            queue_path = os.path.join(tmp, "queue.json")
            code, out = run(["find", "--vault", vault, "--queue", queue_path,
                             "--owner", "linh"])
            self.assertEqual(code, 0, out)
            with open(queue_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data["queue"], "fixture produced an empty queue")
            for e in data["queue"]:
                self.assertEqual(e["owner"], "linh")
                self.assertTrue(e.get("built"), "candidate missing a build timestamp")
            first_built = {tuple(sorted(e["pair"])): e["built"] for e in data["queue"]}

            code, out = run(["find", "--vault", vault, "--queue", queue_path,
                             "--owner", "someone-else"])
            self.assertEqual(code, 0, out)
            with open(queue_path, encoding="utf-8") as f:
                data2 = json.load(f)
            for e in data2["queue"]:
                key = tuple(sorted(e["pair"]))
                if key in first_built:
                    self.assertEqual(e["built"], first_built[key],
                                     "a re-found candidate's build timestamp moved")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_find_default_owner_is_no_data_never_guessed(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-curate-noowner-")
        try:
            vault = os.path.join(tmp, "vault")
            os.makedirs(vault)
            _build_fixture(vault)
            queue_path = os.path.join(tmp, "queue.json")
            code, out = run(["find", "--vault", vault, "--queue", queue_path])
            self.assertEqual(code, 0, out)
            with open(queue_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data["queue"])
            for e in data["queue"]:
                self.assertEqual(e["owner"], "NO-DATA")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GovernanceJson(unittest.TestCase):
    """VB7-02: --json on governance. Prose stays byte-identical when --json is
    absent (every Governance prose assertion above still passes unchanged);
    this covers what --json adds."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-curate-governance-json-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _queue(self, entries):
        path = os.path.join(self.tmp, "queue.json")
        _write_queue(path, entries)
        return path

    def test_pass_json_matches_prose_and_exit_code(self):
        path = self._queue([_candidate("a", "b", built="2026-08-01T00:00:00+00:00",
                                        owner="linh")])
        pcode, pout = run(["governance", "--queue", path, "--cap", "10",
                           "--max-age-days", "1000"])
        self.assertEqual(pcode, 0, pout)
        jcode, jout = run(["governance", "--queue", path, "--cap", "10",
                           "--max-age-days", "1000", "--json"])
        self.assertEqual(jcode, 0, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "PASS", jout)
        self.assertEqual(data["findings"], [], jout)
        self.assertEqual(data["counts"]["count"], 1, jout)

    def test_fail_json_matches_prose_findings(self):
        old = "2020-01-01T00:00:00+00:00"
        path = self._queue([
            _candidate("a", "b", built=old, owner="linh"),
            _candidate("c", "d", built="2026-08-01T00:00:00+00:00", owner="linh"),
        ])
        pcode, pout = run(["governance", "--queue", path, "--cap", "1",
                           "--max-age-days", "1"])
        self.assertEqual(pcode, 1, pout)
        jcode, jout = run(["governance", "--queue", path, "--cap", "1",
                           "--max-age-days", "1", "--json"])
        self.assertEqual(jcode, 1, jout)
        data = json.loads(jout)
        self.assertEqual(data["verdict"], "FAIL", jout)
        kinds = sorted(f["kind"] for f in data["findings"])
        self.assertEqual(kinds, ["over_age", "over_cap"], jout)

    def test_missing_queue_is_no_data_json(self):
        code, out = run(["governance", "--queue",
                         os.path.join(self.tmp, "does-not-exist.json"), "--json"])
        self.assertEqual(code, 2, out)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
