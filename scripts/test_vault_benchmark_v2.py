#!/usr/bin/env python3
"""Calibration for the V2 scoring math.

The checks read live state and change with the estate. The SCORING does not, and
it is the part that can be silently wrong in the direction that flatters us, so
it is the part pinned here. Section 7 of the steering directive exists because a
denominator that quietly drops NO-DATA rewards a system for proving less.
"""
import os
import glob
import json
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import vault_benchmark_v2 as vb  # noqa: E402


def rows(p, f, n):
    out = []
    out += [{"weight": 1, "verdict": vb.PASS} for _ in range(p)]
    out += [{"weight": 1, "verdict": vb.FAIL} for _ in range(f)]
    out += [{"weight": 1, "verdict": vb.NODATA} for _ in range(n)]
    return out


class NoDataStaysInTheDenominator(unittest.TestCase):
    def test_the_directives_own_worked_example(self):
        """7 PASS / 1 FAIL / 12 NO-DATA must NOT outrank 16 PASS / 4 FAIL / 0
        NO-DATA. Under covered_accuracy alone the first looks better (0.875 vs
        0.800), which is precisely the trap."""
        shallow = vb.score(rows(7, 1, 12))
        deep = vb.score(rows(16, 4, 0))
        self.assertGreater(shallow["covered_accuracy"], deep["covered_accuracy"])
        self.assertLess(shallow["proven_score"], deep["proven_score"])
        self.assertLess(shallow["coverage"], deep["coverage"])

    def test_proven_score_counts_nodata_against_you(self):
        self.assertEqual(vb.score(rows(5, 0, 5))["proven_score"], 5.0)

    def test_testing_less_cannot_raise_the_proven_score(self):
        """Turning a FAIL into a NO-DATA, which is what quietly dropping a check
        looks like, must never improve the headline."""
        before = vb.score(rows(5, 5, 0))["proven_score"]
        after = vb.score(rows(5, 0, 5))["proven_score"]
        self.assertEqual(before, after)

    def test_covered_accuracy_is_NO_DATA_when_nothing_was_testable(self):
        self.assertIsNone(vb.score(rows(0, 0, 9))["covered_accuracy"])

    def test_a_crashing_check_is_NO_DATA_never_a_pass(self):
        def boom(ctx):
            raise RuntimeError("nope")
        saved = vb.CHECKS
        vb.CHECKS = [("X01", "explodes", 1, boom)]
        try:
            got, _ = vb.run(os.path.dirname(__file__), os.path.dirname(__file__))
        finally:
            vb.CHECKS = saved
        self.assertEqual(got[0]["verdict"], vb.NODATA)


class D07SeesClaims(unittest.TestCase):
    """The old d07 body returned FAIL unconditionally: the row could never see
    the capability it measures. These pin the rewritten probe in both
    directions: it finds the positive, and it still fails on absence and on a
    dangling locator."""

    @staticmethod
    def ctx(notes, vault="/nonexistent"):
        return {"vault": vault, "tools": "/nonexistent", "notes": notes}

    @staticmethod
    def note(body, front=""):
        return {"path": "x.md", "front": front, "body": body}

    def test_no_claims_still_fails(self):
        got, msg = vb.d07_fact_level_provenance(self.ctx([self.note("plain prose")]))
        self.assertEqual(got, vb.FAIL)
        self.assertIn("no sentence", msg)

    def test_a_resolving_path_claim_passes(self):
        import tempfile, shutil
        tmp = tempfile.mkdtemp(prefix="d07-")
        try:
            with open(os.path.join(tmp, "target.md"), "w") as f:
                f.write("evidence lives here\n")
            n = self.note("claim: the sky is checked [evidence: target.md]")
            got, msg = vb.d07_fact_level_provenance(self.ctx([n], vault=tmp))
            self.assertEqual(got, vb.PASS, msg)
            self.assertIn("1 claim(s)", msg)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_dangling_locator_fails_and_is_worse_than_no_claim(self):
        n = self.note("claim: something [evidence: gone.md]")
        got, msg = vb.d07_fact_level_provenance(self.ctx([n]))
        self.assertEqual(got, vb.FAIL)
        self.assertIn("DANGLING", msg)

    def test_an_id_locator_resolves_against_the_id_index(self):
        holder = self.note("claim: pinned by id [evidence: n-abcdef0123456789]",
                           front="id: n-abcdef0123456789\n")
        got, msg = vb.d07_fact_level_provenance(self.ctx([holder]))
        self.assertEqual(got, vb.PASS, msg)

    def test_repo_and_url_locators_alone_are_NODATA_never_a_pass(self):
        n = self.note("claim: offline unverifiable [evidence: repo:deadbeef]")
        got, msg = vb.d07_fact_level_provenance(self.ctx([n]))
        self.assertEqual(got, vb.NODATA,
                         "a row passed on evidence nobody checked: %s" % msg)

    def test_a_bulleted_and_indented_claim_is_still_seen(self):
        import tempfile, shutil
        tmp = tempfile.mkdtemp(prefix="d07-")
        try:
            with open(os.path.join(tmp, "t.md"), "w") as f:
                f.write("x\n")
            n = self.note("- claim: bulleted [evidence: t.md]\n"
                          "  claim: indented [evidence: t.md]")
            got, msg = vb.d07_fact_level_provenance(self.ctx([n], vault=tmp))
            self.assertEqual(got, vb.PASS, msg)
            self.assertIn("2 claim(s)", msg)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class D06SeesTheCrosswalk(unittest.TestCase):
    """The old d06 body returned NO-DATA unconditionally; the rewrite (adopted
    from the crosswalk lane, tested here at adoption) is structural: an entity
    note with system-qualified source_ids, at least one entity named in two or
    more systems, dangling vault ids failing by name, declarations on plain
    documents failing as the token-gaming shape."""

    @staticmethod
    def ctx(notes):
        return {"vault": "/nonexistent", "tools": "/nonexistent", "notes": notes}

    @staticmethod
    def note(front):
        return {"path": "e.md", "front": front, "body": ""}

    def test_two_system_entity_passes(self):
        n = self.note("entity: repository\nsource_ids: [github:o/r, path:~/x]\n")
        got, msg = vb.d06_entity_crosswalk(self.ctx([n]))
        self.assertEqual(got, vb.PASS, msg)

    def test_one_system_per_entity_crosses_nothing(self):
        n = self.note("entity: repository\nsource_ids: [github:o/r]\n")
        got, _ = vb.d06_entity_crosswalk(self.ctx([n]))
        self.assertEqual(got, vb.FAIL)

    def test_declaration_on_a_plain_document_fails(self):
        n = self.note("source_ids: [github:o/r, path:~/x]\n")
        got, msg = vb.d06_entity_crosswalk(self.ctx([n]))
        self.assertEqual(got, vb.FAIL)
        self.assertIn("without entity", msg)

    def test_a_dangling_vault_id_fails_by_name(self):
        n = self.note("entity: tool\nsource_ids: [vault:n-0000000000000000, path:~/x]\n")
        got, msg = vb.d06_entity_crosswalk(self.ctx([n]))
        self.assertEqual(got, vb.FAIL)
        self.assertIn("DANGLING", msg)

    def test_zero_declarations_is_NODATA_never_a_pass(self):
        got, _ = vb.d06_entity_crosswalk(self.ctx([self.note("entity: tool\n")]))
        self.assertEqual(got, vb.NODATA)


class D09HasAPassBranch(unittest.TestCase):
    """The old d09 body failed even with all five fields present: the third
    unconditional-verdict probe found in one night."""

    @staticmethod
    def ctx(notes):
        return {"vault": "/nonexistent", "tools": "/nonexistent", "notes": notes}

    def test_all_five_present_passes(self):
        n = {"path": "t.md", "body": "", "front":
             "valid_from: 2026-01-01\nvalid_to: 2026-02-01\nobserved_at: 2026-01-01\n"
             "ingested_at: 2026-01-01\nverified_at: 2026-01-02\n"}
        got, msg = vb.d09_bitemporal_facts(self.ctx([n]))
        self.assertEqual(got, vb.PASS, msg)

    def test_a_subset_still_fails(self):
        n = {"path": "t.md", "body": "", "front": "valid_from: 2026-01-01\n"}
        got, _ = vb.d09_bitemporal_facts(self.ctx([n]))
        self.assertEqual(got, vb.FAIL)


class D04ReadsTheRealRun(unittest.TestCase):
    """The old d04 body returned NO-DATA unconditionally, the fourth
    unconditional-verdict probe found in one night."""

    def _write(self, rows):
        import json, tempfile
        d = tempfile.mkdtemp(prefix="d04-")
        p = os.path.join(d, "results-2026-01-01.json")
        with open(p, "w") as f:
            json.dump({"rows": rows}, f)
        return os.path.join(d, "results-*.json")

    @staticmethod
    def row(task, memory, success):
        return {"task_id": task, "memory": memory, "success": success}

    def test_a_paired_run_with_a_lift_passes(self):
        g = self._write([self.row("t1", "off", False), self.row("t1", "on", True),
                         self.row("t2", "off", True), self.row("t2", "on", True)])
        got, msg = vb.d04_memory_outcome_lift({"results_glob": g})
        self.assertEqual(got, vb.PASS, msg)
        self.assertIn("1 gained", msg)

    def test_an_unpaired_run_is_NODATA_never_a_measurement(self):
        g = self._write([self.row("t1", "off", False)])
        got, msg = vb.d04_memory_outcome_lift({"results_glob": g})
        self.assertEqual(got, vb.NODATA, msg)

    def test_no_lift_is_a_FAIL_not_missing_data(self):
        g = self._write([self.row("t1", "off", True), self.row("t1", "on", False)])
        got, msg = vb.d04_memory_outcome_lift({"results_glob": g})
        self.assertEqual(got, vb.FAIL, msg)

    def test_the_repo_own_results_file_reads_as_a_lift(self):
        got, msg = vb.d04_memory_outcome_lift({})
        self.assertEqual(got, vb.PASS, msg)
        self.assertIn("7 gained", msg)

    def test_a_non_dict_json_top_level_is_NODATA_never_a_crash(self):
        """A results file that parses as JSON but is not {"rows": [...]}
        (a bare list, here) used to raise AttributeError from .get() off a
        list. Schema mismatch is NO-DATA, never an uncaught exception."""
        import json, tempfile
        d = tempfile.mkdtemp(prefix="d04-badtop-")
        p = os.path.join(d, "results-2026-01-01.json")
        with open(p, "w") as f:
            json.dump([1, 2, 3], f)
        got, msg = vb.d04_memory_outcome_lift({"results_glob": os.path.join(d, "results-*.json")})
        self.assertEqual(got, vb.NODATA, msg)

    def test_rows_not_a_list_is_NODATA_never_a_crash(self):
        import json, tempfile
        d = tempfile.mkdtemp(prefix="d04-badrows-")
        p = os.path.join(d, "results-2026-01-01.json")
        with open(p, "w") as f:
            json.dump({"rows": "not-a-list"}, f)
        got, msg = vb.d04_memory_outcome_lift({"results_glob": os.path.join(d, "results-*.json")})
        self.assertEqual(got, vb.NODATA, msg)

    def test_the_repo_own_results_file_carries_its_provenance(self):
        """The bundle ships this exact file (VB3-01); its provenance must be
        readable straight off the fixture, not asserted only in prose:
        11 paired tasks, measured 2026-08-30 (the filename's own date)."""
        here = os.path.dirname(os.path.abspath(vb.__file__))
        candidates = sorted(glob.glob(os.path.join(
            here, "..", "benchmarks", "memory-ab", "results-*.json")))
        self.assertTrue(candidates, "no shipped memory-ab results file found")
        path = candidates[-1]
        self.assertIn("2026-08-30", os.path.basename(path))
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)["rows"]
        task_ids = {r["task_id"] for r in rows}
        self.assertEqual(len(task_ids), 11, task_ids)


class D15ReadsTheComparison(unittest.TestCase):
    """The d15 tail returned NO-DATA whenever retrieval touched links; with the
    measurement on disk the probe now reads it structurally, and refuses the
    shapes that prove nothing."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="d15-")
        # ctx["tools"] must hold a bm_vault.py that touches links, or the probe
        # exits on the earlier branch before reaching the results file.
        with open(os.path.join(self.dir, "bm_vault.py"), "w") as f:
            f.write("# JOIN links stub for the probe's earlier branch\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, rows, summary, verdict, calibration="calibration PASS both directions"):
        import json
        p = os.path.join(self.dir, "results-2026-01-01.json")
        with open(p, "w") as f:
            json.dump({"rows": rows, "summary": summary, "verdict": verdict,
                       "calibration": calibration}, f)
        return {"tools": self.dir, "vault": "/nonexistent", "notes": [],
                "graph_results_glob": os.path.join(self.dir, "results-*.json")}

    @staticmethod
    def row(q, arm, success):
        return {"query_id": q, "arm": arm, "success": success}

    def _rows(self, n_graph_only):
        rows = []
        for i in range(8):
            rows.append(self.row("q%d" % i, "flat", False))
            rows.append(self.row("q%d" % i, "graph", i < n_graph_only))
        return rows

    def test_a_consistent_demanded_verdict_passes(self):
        rows = self._rows(3)
        ctx = self._write(rows, {"queries": 8, "flat_success": 0, "graph_success": 3,
                                 "graph_only": ["q0", "q1", "q2"]},
                          "MEASURED USE CASE DEMANDS THE GRAPH: three queries")
        got, msg = vb.d15_graph_value_proven(ctx)
        self.assertEqual(got, vb.PASS, msg)

    def test_a_verdict_contradicting_its_rows_fails(self):
        rows = self._rows(3)
        ctx = self._write(rows, {"queries": 8, "flat_success": 0, "graph_success": 3,
                                 "graph_only": ["q0", "q1", "q2"]},
                          "NO MEASURED USE CASE DEMANDS THE GRAPH")
        got, msg = vb.d15_graph_value_proven(ctx)
        self.assertEqual(got, vb.FAIL, msg)

    def test_an_unpaired_run_is_NODATA(self):
        rows = self._rows(2)[:-1]
        ctx = self._write(rows, {"queries": 8, "flat_success": 0, "graph_success": 2,
                                 "graph_only": ["q0", "q1"]},
                          "MEASURED USE CASE DEMANDS THE GRAPH")
        got, msg = vb.d15_graph_value_proven(ctx)
        self.assertEqual(got, vb.NODATA, msg)

    def test_d13_reconciles_the_index_against_disk(self):
        # Grouped here for the shared refusal doctrine; three shapes in one
        # test method to keep the fixture cheap: clean PASS, stale FAIL naming
        # the row, absent index NODATA.
        import sqlite3, tempfile, shutil
        d = tempfile.mkdtemp(prefix="d13-")
        try:
            live = os.path.join(d, "live.md")
            with open(live, "w") as f:
                f.write("x\n")
            idx = os.path.join(d, "ix.sqlite3")
            con = sqlite3.connect(idx)
            con.execute("CREATE TABLE notes (path TEXT)")
            con.execute("INSERT INTO notes VALUES (?)", (live,))
            con.commit()
            got, msg = vb.d13_retention_deletion_propagation({"index_path": idx})
            self.assertEqual(got, vb.PASS, msg)
            con.execute("INSERT INTO notes VALUES (?)", (os.path.join(d, "gone.md"),))
            con.commit()
            got, msg = vb.d13_retention_deletion_propagation({"index_path": idx})
            self.assertEqual(got, vb.FAIL, msg)
            self.assertIn("gone.md", msg)
            got, _ = vb.d13_retention_deletion_propagation(
                {"index_path": os.path.join(d, "absent.sqlite3")})
            self.assertEqual(got, vb.NODATA)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_calibration_is_NODATA(self):
        rows = self._rows(3)
        ctx = self._write(rows, {"queries": 8, "flat_success": 0, "graph_success": 3,
                                 "graph_only": ["q0", "q1", "q2"]},
                          "MEASURED USE CASE DEMANDS THE GRAPH", calibration="")
        got, msg = vb.d15_graph_value_proven(ctx)
        self.assertEqual(got, vb.NODATA, msg)


if __name__ == "__main__":
    unittest.main()
