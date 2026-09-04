"""What the Work contract must refuse.

Refusal is the feature. A Work record that accepts anything is a file format
rather than a contract, and every clause here exists because something
downstream breaks without it, usually invisibly and usually at run time.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work_record as W  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def unit(**kw):
    d = {"id": "U1", "title": "a unit", "done_check": "pytest",
         "owns": ["a.py"], "depends_on": []}
    d.update(kw)
    return d


class EveryClauseExistsBecauseSomethingBreaksWithoutIt(unittest.TestCase):
    def test_a_unit_with_no_done_check_is_refused(self):
        rec, problems = W.create("o", [unit(done_check="")])
        self.assertIsNone(rec)
        self.assertIn("nothing to verify", problems[0])

    def test_a_unit_with_no_write_scope_is_refused(self):
        """The scope audit returns NO-DATA without one, which blocks
        integration, so an undeclared unit should never be dispatched."""
        rec, problems = W.create("o", [unit(owns=[])])
        self.assertIsNone(rec)
        self.assertIn("NO-DATA", problems[0])

    def test_a_write_scope_escaping_the_repository_is_refused(self):
        """git status cannot see a write outside the tree, so an escaping
        scope would be invisible to the audit and every control after it.
        Found live: a unit declaring ../escaped.txt reported INTEGRATED at
        exit 0 while its file landed outside the repository."""
        for bad in ("../escaped.txt", "/tmp/escaped.txt", "a/../../up.txt"):
            rec, problems = W.create("o", [unit(owns=[bad])])
            self.assertIsNone(rec, bad)
            self.assertIn("escaping the repository", problems[0])

    def test_a_dotdot_inside_the_tree_is_still_accepted(self):
        rec, problems = W.create("o", [unit(owns=["a/../b.txt"])])
        self.assertEqual(problems, [])
        self.assertIsNotNone(rec)

    def test_a_dangling_dependency_is_refused(self):
        """Invisible at run time: it drops the unit from the ready set forever
        and nothing says so."""
        rec, problems = W.create("o", [unit(depends_on=["GHOST"])])
        self.assertIsNone(rec)
        self.assertIn("GHOST", problems[0])

    def test_a_cycle_is_refused_and_the_cycle_is_named(self):
        rec, problems = W.create("o", [
            unit(id="A", owns=["a"], depends_on=["B"]),
            unit(id="B", owns=["b"], depends_on=["A"])])
        self.assertIsNone(rec)
        self.assertTrue(any("A -> B -> A" in p for p in problems), problems)

    def test_a_longer_cycle_is_also_caught(self):
        rec, problems = W.create("o", [
            unit(id="A", owns=["a"], depends_on=["C"]),
            unit(id="B", owns=["b"], depends_on=["A"]),
            unit(id="C", owns=["c"], depends_on=["B"])])
        self.assertIsNone(rec)
        self.assertTrue(any("cycle" in p for p in problems))

    def test_two_units_sharing_an_id_are_refused(self):
        """Claims are keyed by id, so this is two workers on one lease."""
        rec, problems = W.create("o", [unit(id="U1"), unit(id="U1", owns=["b"])])
        self.assertIsNone(rec)
        self.assertIn("twice", problems[0])

    def test_no_units_at_all_is_refused(self):
        rec, problems = W.create("o", [])
        self.assertIsNone(rec)
        self.assertIn("not Work yet", problems[0])

    def test_no_outcome_is_refused(self):
        rec, problems = W.create("", [unit()])
        self.assertIsNone(rec)
        self.assertIn("nothing says what this Work is for", problems[0])

    def test_an_evidence_family_outside_the_library_is_refused(self):
        """The persona document's universal evidence library is E1 to E18.
        A code outside it is a typo or an invention, and a unit carrying one
        would be scheduled with an evidence plan nothing downstream can read."""
        rec, problems = W.create("o", [unit(evidence_family="E99")])
        self.assertIsNone(rec)
        self.assertIn("not one of E1 to E18", problems[0])

    def test_an_oracle_source_outside_the_vocabulary_is_refused(self):
        """independence_for() reads oracle_source to decide whether a PASS
        was independent or circular. An unrecognized value reads
        "independent" by falling through, which is the wrong answer stated
        confidently, so it is refused at declaration instead."""
        rec, problems = W.create("o", [unit(oracle_source="guesswork")])
        self.assertIsNone(rec)
        self.assertIn("recognized oracle sources", problems[0])

    def test_the_declared_vocabulary_itself_is_accepted(self):
        """The positive control: the two refusals above must be about the
        VALUE, not about declaring these fields at all."""
        rec, problems = W.create(
            "o", [unit(evidence_family="E7", oracle_source="requirement")])
        self.assertEqual(problems, [])
        self.assertIsNotNone(rec)


class ItReportsEVERYProblemNotTheFirst(unittest.TestCase):
    """A caller fixing one at a time learns the contract slowly, by being
    refused over and over."""

    def test_several_problems_come_back_together(self):
        _rec, problems = W.create("o", [
            unit(id="", done_check="", owns=[])])
        self.assertGreaterEqual(len(problems), 3)


class AWellFormedUnitIsAccepted(unittest.TestCase):
    def test_it_is_accepted_with_no_problems(self):
        rec, problems = W.create("a real outcome", [unit()])
        self.assertEqual(problems, [])
        self.assertIsNotNone(rec)

    def test_the_record_carries_the_outcome_and_a_work_id(self):
        rec, _ = W.create("invoices stop double posting", [unit()])
        self.assertEqual(rec["outcome"], "invoices stop double posting")
        self.assertTrue(rec["work_id"].startswith("W-"))

    def test_it_is_written_in_the_shape_the_scheduler_already_reads(self):
        """A second shape would need a second scheduler."""
        rec, _ = W.create("o", [unit()])
        self.assertIn("rows", rec)
        self.assertIn("features", rec)
        for key in ("id", "title", "status", "depends_on", "owns", "done_check"):
            self.assertIn(key, rec["rows"][0])

    def test_storing_it_writes_a_file_the_scheduler_can_load(self):
        d = tempfile.mkdtemp()
        rec, _ = W.create("o", [unit()], store=d)
        with open(rec["path"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["rows"][0]["id"], "U1")


class TheRealSchedulerReadsIt(unittest.TestCase):
    """The point of the whole module. If graph_loop cannot compute a ready set
    from this, the contract is the wrong shape and everything else is moot."""

    def test_the_scheduler_computes_a_ready_set_and_holds_the_dependent_unit(self):
        import graph_loop
        d = tempfile.mkdtemp()
        rec, problems = W.create("an outcome", [
            unit(id="U1", owns=["a.py"]),
            unit(id="U2", owns=["b.py"]),
            unit(id="U3", owns=["c.py"], depends_on=["U1", "U2"])], store=d)
        self.assertEqual(problems, [])
        plan = graph_loop.plan(graph_loop.load(rec["path"]), slots=3)
        self.assertEqual(sorted(n["id"] for n in plan["batch"]), ["U1", "U2"])
        blocked = [b[0]["id"] for b in plan["blocked"]]
        self.assertEqual(blocked, ["U3"])

    def test_nothing_is_silently_dropped(self):
        """A unit that is neither ready nor reported is the failure the dangling
        edge clause exists to prevent, so it is checked here too."""
        import graph_loop
        d = tempfile.mkdtemp()
        rec, _ = W.create("o", [unit(id="U1", owns=["a"]),
                                unit(id="U2", owns=["b"], depends_on=["U1"])],
                          store=d)
        plan = graph_loop.plan(graph_loop.load(rec["path"]), slots=3)
        accounted = ({n["id"] for n in plan["batch"]}
                     | {b[0]["id"] for b in plan["blocked"]}
                     | {n["id"] for n in plan.get("deferred", [])})
        self.assertEqual(accounted, {"U1", "U2"})
        self.assertEqual(plan.get("unknown_deps"), [])


class ItDoesNotPretendToDecomposeEnglish(unittest.TestCase):
    """The opposite claim would be the easiest lie on this board. A script that
    turned a sentence into units would produce confident nonsense with a
    done_check attached, which is worse than refusing."""

    def test_an_outcome_with_no_units_is_NO_DATA_from_the_command(self):
        self.assertEqual(W.main(["an outcome with no decomposition"]), 2)

    def test_no_outcome_at_all_is_NO_DATA(self):
        self.assertEqual(W.main([]), 2)

    def test_the_module_exposes_no_decomposer(self):
        for name in ("decompose", "split", "infer_units", "plan_from_outcome"):
            self.assertFalse(hasattr(W, name), name)


#: A child that starts writing a Work document and is SIGKILLed part way
#: through the JSON, which is the run-interrupted-mid-stamp case E61 exists
#: for. `mode` picks the writer: "helper" is work_record.write_record,
#: "plain" is the open(path, "w") plus json.dump every stamp used to be, and
#: is the POSITIVE CONTROL: it must tear the file, or this test proves
#: nothing about the helper that does not.
KILLED_WRITER = '''
import json, os, signal, sys
sys.path.insert(0, %r)
import work_record as W

path, mode = sys.argv[1], sys.argv[2]
real_dump = json.dump


def dump_then_die(obj, fh, **kw):
    # Half a document, flushed to the file the writer chose, then gone. No
    # finally block runs after SIGKILL, which is the point.
    fh.write('{"outcome": "half a doc')
    fh.flush()
    os.kill(os.getpid(), signal.SIGKILL)


json.dump = dump_then_die
if mode == "helper":
    W.write_record(path, {"outcome": "the new one"})
else:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"outcome": "the new one"}, fh, indent=1)
'''


class AKilledStampLeavesAWholeDocument(unittest.TestCase):
    """E61. The claim store in the same run directory already survived a kill
    mid-write; the Work document beside it did not, because every one of its
    writers truncated the real file first."""

    def _kill_mid_write(self, mode):
        here = os.path.dirname(os.path.abspath(__file__))
        d = tempfile.mkdtemp()
        path = os.path.join(d, "W-kill.json")
        before = {"outcome": "the one already on disk", "rows": [{"id": "U1"}]}
        W.write_record(path, before)
        child = os.path.join(d, "child.py")
        with open(child, "w", encoding="utf-8") as fh:
            fh.write(KILLED_WRITER % here)
        proc = subprocess.run([sys.executable, child, path, mode],
                              capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, -9,
                         "the child was meant to die on SIGKILL, got %r: %s"
                         % (proc.returncode, proc.stderr.decode()[-400:]))
        with open(path, encoding="utf-8") as fh:
            return before, fh.read()

    def test_the_helper_leaves_the_whole_previous_document(self):
        before, raw = self._kill_mid_write("helper")
        self.assertEqual(json.loads(raw), before)

    def test_the_plain_rewrite_it_replaced_tears_the_file(self):
        """The control. Without this the test above would pass on a machine
        where nothing was ever at risk."""
        _, raw = self._kill_mid_write("plain")
        with self.assertRaises(ValueError):
            json.loads(raw)


class NoStampBypassesTheHelper(unittest.TestCase):
    """The done-check clause, kept runnable rather than run once by hand."""

    def test_brother_run_never_opens_the_record_path_for_writing(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "brother_run.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(re.findall(r'open\(record_path,\s*"w"', src), [])
        self.assertGreaterEqual(src.count("work_record.write_record("), 10)


if __name__ == "__main__":
    unittest.main()
