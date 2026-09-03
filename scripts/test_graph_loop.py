"""Calibration for scripts/graph_loop.py.

The property under test is not that a plan is produced. It is that two nodes
which would put two writers on one path are NEVER in the same batch. On
2026-08-29 this estate lost about 500 lines, spent an hour establishing who owned
83 lines, and had two efforts fix the same defect within an hour. Every one of
those was a concurrency failure that a dependency edge cannot express.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import graph_loop as gl  # noqa: E402


def node(nid, deps=None, owns=None, status='SCHEDULED', hours=1, in_ship=True):
    # owns defaults to [] (declared read-only) rather than None, so a fixture
    # never accidentally exercises the undeclared-scope path.
    return {'id': nid, 'title': nid, 'status': status,
            'depends_on': deps or [], 'owns': [] if owns is None else owns,
            'effort_hours': hours, 'in_ship_v1': in_ship}


def doc(rows):
    return {'rows': rows, 'features': []}


class Conflicts(unittest.TestCase):
    def test_identical_paths_conflict(self):
        self.assertTrue(gl.conflicts({'owns': ['a/b.py']}, {'owns': ['a/b.py']}))

    def test_disjoint_paths_do_not_conflict(self):
        self.assertFalse(gl.conflicts({'owns': ['a/b.py']}, {'owns': ['c/d.py']}))

    def test_a_directory_conflicts_with_a_file_inside_it(self):
        """Owning a directory owns what is in it. Missing this is how one agent
        holding docs/plan/examples and another holding one file in it both look
        clear."""
        self.assertTrue(gl.conflicts({'owns': ['docs/plan/examples']},
                                     {'owns': ['docs/plan/examples/one.md']}))

    def test_the_containment_check_works_in_both_directions(self):
        self.assertTrue(gl.conflicts({'owns': ['docs/plan/examples/one.md']},
                                     {'owns': ['docs/plan/examples']}))

    def test_a_shared_prefix_that_is_not_a_directory_does_NOT_conflict(self):
        """scripts/intake_score.py and scripts/intake_scorer.py are different
        files. A naive startswith would wrongly serialize them forever."""
        self.assertFalse(gl.conflicts({'owns': ['scripts/intake_score.py']},
                                      {'owns': ['scripts/intake_scorer.py']}))

    def test_a_node_owning_nothing_conflicts_with_nobody(self):
        """A read-only node can always join a batch."""
        self.assertFalse(gl.conflicts({'owns': []}, {'owns': ['a/b.py']}))


class Batching(unittest.TestCase):
    def test_two_disjoint_ready_nodes_run_together(self):
        p = gl.plan(doc([node('A', owns=['x.py']), node('B', owns=['y.py'])]), slots=4)
        self.assertEqual(sorted(n['id'] for n in p['batch']), ['A', 'B'])

    def test_two_CONFLICTING_ready_nodes_are_NOT_batched(self):
        """THE LOAD-BEARING TEST. Without this the scheduler is a sorter."""
        p = gl.plan(doc([node('A', owns=['x.py']), node('B', owns=['x.py'])]), slots=4)
        self.assertEqual(len(p['batch']), 1)
        self.assertEqual(len(p['deferred']), 1)
        self.assertIn('overlaps', p['deferred'][0][1])

    def test_a_ready_node_conflicting_with_something_IN_FLIGHT_is_deferred(self):
        """The in-flight case is the one that actually bit today: an agent was
        already holding the path when the next dispatch was chosen."""
        p = gl.plan(doc([node('A', owns=['x.py'], status='IN-FLIGHT'),
                         node('B', owns=['x.py'])]), slots=4)
        self.assertEqual(p['batch'], [])
        self.assertIn('overlaps A', p['deferred'][0][1])

    def test_capacity_caps_the_batch_even_with_no_conflicts(self):
        p = gl.plan(doc([node('A', owns=['a']), node('B', owns=['b']),
                         node('C', owns=['c'])]), slots=2)
        self.assertEqual(len(p['batch']), 2)

    def test_zero_capacity_dispatches_nothing(self):
        """The refuse band must actually refuse, not merely warn."""
        p = gl.plan(doc([node('A', owns=['a'])]), slots=0)
        self.assertEqual(p['batch'], [])


class UndeclaredScope(unittest.TestCase):
    """owns=None and owns=[] are DIFFERENT states. The first two drafts of this
    scheduler collapsed them, so a node that had simply never declared its paths
    was batched beside anything, which is the undeclared write that destroyed
    about 500 lines in this estate on 2026-08-29."""

    def test_a_node_with_NO_declared_scope_is_NOT_dispatched(self):
        d = {'rows': [{'id': 'A', 'title': 'A', 'status': 'SCHEDULED',
                       'depends_on': [], 'effort_hours': 1, 'in_ship_v1': True}],
             'features': []}
        p = gl.plan(d, slots=4)
        self.assertEqual(p['batch'], [])
        self.assertIn('NO DECLARED SCOPE', p['deferred'][0][1])

    def test_a_node_declared_READ_ONLY_is_dispatched(self):
        """owns=[] is a declaration, not an absence, and must still run."""
        d = doc([node('A', owns=[])])
        self.assertEqual([n['id'] for n in gl.plan(d, slots=4)['batch']], ['A'])

    def test_read_only_and_a_writer_may_share_a_batch(self):
        d = doc([node('A', owns=[]), node('B', owns=['x.py'])])
        self.assertEqual(len(gl.plan(d, slots=4)['batch']), 2)


class FounderGate(unittest.TestCase):
    def test_a_founder_owned_node_is_never_pulled(self):
        d = doc([node('A', owns=['x.py'])])
        d['rows'][0]['owner'] = 'FOUNDER'
        p = gl.plan(d, slots=4)
        self.assertEqual(p['batch'], [])
        self.assertIn('FOUNDER-GATED', p['deferred'][0][1])

    def test_an_AWAITING_FOUNDER_node_is_never_pulled(self):
        d = doc([node('A', owns=['x.py'], status='AWAITING FOUNDER')])
        self.assertEqual(gl.plan(d, slots=4)['batch'], [])


class EstateCap(unittest.TestCase):
    def test_the_estate_builder_cap_beats_a_larger_hardware_capacity(self):
        """A resource check that quietly outvotes a written rule is worse than
        no resource check: it looks principled while removing a control."""
        self.assertEqual(gl.ESTATE_BUILDER_CAP, 3)
        cap, _notes = gl.machine_capacity()
        self.assertLessEqual(min(cap, gl.ESTATE_BUILDER_CAP), 3)


class DiskBandVersusExplicitSlots(unittest.TestCase):
    """The rule this class exists to prove, from a live incident: this
    machine sat at 8.8-8.9 GiB free, under graph_loop.py's own DISK_CLEANUP_
    GIB (15), so machine_capacity() legitimately dropped to 1 slot and three
    concurrency proofs (scripts/test_spine.py, scripts/test_crash_resume.py,
    scripts/acceptance_9.py) went red for a reason that had nothing to do
    with their own logic: they need TWO independent nodes claimed in one
    batch, and the host happened to be scarce.

    TESTS OF CONCURRENCY OWN THEIR SLOT COUNT (an explicit slots= must pin
    capacity regardless of the host). TESTS OF CAPACITY POLICY OWN THE
    POLICY (an unpinned plan() call must still obey the disk band; that
    policy stays test_resource_gate.py's to prove). Both are checked here,
    against the SAME injected scarce reading, so neither the pin nor the
    band can silently stop working: forced with a mocked shutil.disk_usage,
    never by depending on this machine's real free space, exactly like
    test_resource_gate.py's own injected readings.
    """

    def _cleanup_band_disk_usage(self):
        # A total large enough that "used" is never negative; only "free"
        # (the third element the code unpacks) matters to machine_capacity.
        free = int((gl.DISK_CLEANUP_GIB - 1) * 1024 ** 3)
        total = 500 * 1024 ** 3
        return (total, total - free, free)

    def test_an_unpinned_plan_still_drops_to_one_slot_under_the_band(self):
        """THE REAL SCHEDULER'S BEHAVIOR MUST NOT CHANGE: a caller that
        never names slots= (graph_loop.py's own CLI default) still gets the
        disk band's protection."""
        d = doc([node('A', owns=['a']), node('B', owns=['b'])])
        with unittest.mock.patch.object(
                gl.shutil, 'disk_usage',
                return_value=self._cleanup_band_disk_usage()):
            p = gl.plan(d, slots=None)
        self.assertEqual(p['capacity'], 1)
        self.assertEqual(len(p['batch']), 1)

    def test_a_pinned_slot_count_survives_the_same_scarce_disk(self):
        """A concurrency test's own --slots must not be quietly overridden
        by whatever the host's disk happens to read."""
        d = doc([node('A', owns=['a']), node('B', owns=['b'])])
        with unittest.mock.patch.object(
                gl.shutil, 'disk_usage',
                return_value=self._cleanup_band_disk_usage()):
            p = gl.plan(d, slots=2)
        self.assertEqual(p['capacity'], 2)
        self.assertEqual(sorted(n['id'] for n in p['batch']), ['A', 'B'])


class Ordering(unittest.TestCase):
    def test_downstream_weight_counts_transitive_dependents(self):
        d = doc([node('A'), node('B', deps=['A']), node('C', deps=['B'])])
        self.assertEqual(gl.plan(d, slots=1)['weight']['A'], 2)

    def test_the_node_unblocking_most_is_dispatched_first(self):
        """Dagster's asset-graph prioritisation, and the rule this estate had
        written down and was not using: today's dispatches were picked by
        intuition rather than by what they free."""
        d = doc([node('heavy', owns=['h']), node('key', owns=['k']),
                 node('x', deps=['key']), node('y', deps=['key'])])
        self.assertEqual(gl.plan(d, slots=1)['batch'][0]['id'], 'key')

    def test_a_node_IN_the_ship_beats_a_bigger_one_outside_it(self):
        """Added after the first draft proposed a 40 hour node outside the ship
        ahead of a 6 hour node inside it, because the big one unblocked more.
        Unblocking is the right tiebreak WITHIN a commitment and the wrong
        primary key once a date is named: a scheduler optimising purely for
        graph structure drifts toward the largest subtree, which is how a
        deadline dies by a sequence of individually defensible choices."""
        d = doc([node('outside', owns=['a'], hours=40, in_ship=False),
                 node('x', deps=['outside']), node('y', deps=['outside']),
                 node('inside', owns=['b'], hours=6, in_ship=True)])
        self.assertEqual(gl.plan(d, slots=1)['batch'][0]['id'], 'inside')

    def test_a_tie_breaks_toward_the_cheaper_node(self):
        d = doc([node('big', owns=['a'], hours=40), node('small', owns=['b'], hours=1)])
        self.assertEqual(gl.plan(d, slots=1)['batch'][0]['id'], 'small')


class BlockedAndBroken(unittest.TestCase):
    def test_a_blocked_node_NAMES_what_it_waits_on(self):
        d = doc([node('A'), node('B', deps=['A'])])
        self.assertEqual(gl.plan(d, slots=4)['blocked'][0][1], ['A'])

    def test_a_dependency_naming_no_node_is_reported_as_a_broken_graph(self):
        """Silently ignoring it makes a node look READY when its prerequisite
        was deleted or renamed."""
        d = doc([node('A', deps=['GHOST'])])
        self.assertEqual(gl.plan(d, slots=4)['unknown_deps'], [('A', 'GHOST')])

    def test_a_dependency_cycle_does_not_hang(self):
        d = doc([node('A', deps=['B']), node('B', deps=['A'])])
        self.assertIsInstance(gl.plan(d, slots=4)['weight'], dict)


class RealRoadmap(unittest.TestCase):
    def test_the_real_roadmap_produces_a_plan(self):
        self.assertEqual(gl.main(['--slots', '2']), 0)

    def test_the_real_roadmap_has_no_dangling_dependency(self):
        self.assertEqual(gl.plan(gl.load(), slots=1)['unknown_deps'], [])

    def test_no_two_nodes_in_a_real_batch_share_a_path(self):
        """The invariant, asserted over the live board rather than a fixture."""
        p = gl.plan(gl.load(), slots=8)
        for i, a in enumerate(p['batch']):
            for b in p['batch'][i + 1:]:
                self.assertFalse(gl.conflicts(a, b),
                                 '%s and %s share a path' % (a['id'], b['id']))

    def test_an_unreadable_roadmap_is_NO_DATA_not_a_pass(self):
        saved = gl.ROADMAP
        try:
            gl.ROADMAP = os.path.join(tempfile.gettempdir(), 'no-such-roadmap-xyz.json')
            self.assertEqual(gl.main([]), 2)
        finally:
            gl.ROADMAP = saved


class WriteSetsAreQualifiedByRepository(unittest.TestCase):
    """Until 2026-08-29 this scheduler compared BARE paths, so two nodes owning
    tools/x.py in two different repositories looked like a collision and were
    serialised for nothing, while a genuine shared file could look unrelated if
    one node wrote it qualified and the other did not. An outside review named
    it and this board had recorded the same defect independently."""

    def test_the_same_name_in_DIFFERENT_repositories_does_not_conflict(self):
        a = {"owns": ["tools/x.py"], "repo": "RepoOne"}
        b = {"owns": ["tools/x.py"], "repo": "RepoTwo"}
        self.assertFalse(gl.conflicts(a, b))

    def test_the_SAME_file_still_conflicts_when_one_side_is_qualified(self):
        """The dangerous direction: a real collision must not be missed just
        because the two nodes spelled the path differently."""
        a = {"owns": ["tools/x.py"], "repo": "RepoOne"}
        b = {"owns": ["RepoOne:tools/x.py"]}
        self.assertTrue(gl.conflicts(a, b))

    def test_two_bare_paths_still_conflict(self):
        """Unqualified on both sides means the same default repository, so the
        old behaviour is preserved for every node that never declares one."""
        self.assertTrue(gl.conflicts({"owns": ["scripts/a.py"]},
                                        {"owns": ["scripts/a.py"]}))

    def test_a_directory_still_covers_a_file_under_it_within_one_repo(self):
        a = {"owns": ["scripts/"], "repo": "R"}
        b = {"owns": ["scripts/a.py"], "repo": "R"}
        self.assertTrue(gl.conflicts(a, b))

    def test_a_directory_in_ANOTHER_repository_does_not_cover_it(self):
        a = {"owns": ["scripts/"], "repo": "RepoOne"}
        b = {"owns": ["scripts/a.py"], "repo": "RepoTwo"}
        self.assertFalse(gl.conflicts(a, b))

    def test_qualify_reads_both_spellings(self):
        self.assertEqual(gl.qualify("R:tools/x.py"), ("R", "tools/x.py"))
        self.assertEqual(gl.qualify("tools/x.py", "R"), ("R", "tools/x.py"))

    def test_an_unqualified_path_with_no_repo_gets_the_default_not_a_wildcard(self):
        """Guessing beyond the default would invent a repository, and a wildcard
        would make every bare path collide with everything."""
        repo, path = gl.qualify("tools/x.py")
        self.assertEqual(repo, gl.DEFAULT_REPO)
        self.assertEqual(path, "tools/x.py")

    def test_a_node_owning_nothing_still_conflicts_with_nobody(self):
        self.assertFalse(gl.conflicts({"owns": []}, {"owns": ["a.py"]}))


class TheSchedulerCanBePointedAtAnyGraph(unittest.TestCase):
    """FOUNDER ASK 2026-08-29: run a graph loop over the VAULT stream, which is
    not the readiness roadmap.

    load() has always taken a path. main() never passed one, so the scheduler
    could only ever schedule ONE hardcoded file. Every other stream's choice was
    to fork the script or edit a module constant, and both of those turn a shared
    control into a per-stream copy that drifts. The gap was in the argument
    parser, not in the scheduling logic, which is why the fix is one flag rather
    than a second tool.
    """

    def _write(self, tmp, nid):
        path = os.path.join(tmp, 'g.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'rows': [node(nid, owns=['a.py'])]}, fh)
        return path

    def test_a_named_graph_is_the_one_actually_scheduled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'ONLY-IN-THE-NAMED-FILE')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = gl.main(['--roadmap', path, '--slots', '1'])
            self.assertEqual(rc, 0)
            self.assertIn('ONLY-IN-THE-NAMED-FILE', buf.getvalue())

    def test_a_named_graph_that_cannot_be_READ_is_NO_DATA_never_a_pass(self):
        """The estate's own law: NO-DATA is never a pass. A scheduler that
        silently fell back to the default roadmap would dispatch the WRONG
        stream's work while looking like it had read yours."""
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = gl.main(['--roadmap', os.path.join('/nonexistent-dir', 'nope.json')])
        self.assertEqual(rc, 2)
        self.assertNotIn('DISPATCH NOW', buf.getvalue())

    def test_omitting_the_flag_still_reads_the_DEFAULT_roadmap(self):
        """The flag ADDS a capability. A change that also moved the default
        would silently repoint every existing caller."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, 'SHOULD-NOT-APPEAR')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = gl.main(['--slots', '1'])
            self.assertEqual(rc, 0)
            self.assertNotIn('SHOULD-NOT-APPEAR', buf.getvalue())


class AnEventNodeIsNeverPulledByASession(unittest.TestCase):
    """The ready-set standard (docs/plan/READY-SET-STANDARD-2026-08-28.md) has
    specified event nodes since 2026-08-28: "an external verdict the node waits
    on (a founder merge, a team retest). An event node is never pulled by a
    session." This scheduler's own docstring lists event nodes among the five
    practices it did NOT implement.

    Found by modelling real work rather than by reading the standard: the vault
    graph has three nodes waiting on verdicts nobody in this session can produce
    (a peer session's commit, the founder's merges, a read-only Bitbucket
    workspace). Without this, each one either renders as dispatchable (a session
    pulls work it cannot finish) or gets mislabelled FOUNDER-GATED, which names
    the wrong person.
    """

    def _ev(self, **kw):
        n = node('EV', **kw)
        n['event'] = kw.pop('event', 'a peer session commits its staged files')
        return n

    def test_a_node_awaiting_an_event_is_NOT_dispatched(self):
        n = node('EV', owns=['a.py'])
        n['event'] = 'the founder merges PR 68'
        p = gl.plan({'rows': [n]}, slots=3)
        self.assertEqual([b['id'] for b in p['batch']], [])

    def test_the_event_is_NAMED_in_the_deferral_not_just_refused(self):
        """A refusal that does not say what would lift it is indistinguishable
        from a broken graph."""
        n = node('EV', owns=['a.py'])
        n['event'] = 'the founder merges PR 68'
        p = gl.plan({'rows': [n]}, slots=3)
        why = dict((x['id'], w) for x, w in p['deferred'])
        self.assertIn('EV', why)
        self.assertIn('the founder merges PR 68', why['EV'])

    def test_an_event_node_does_NOT_report_as_founder_gated(self):
        """Naming the wrong owner sends the founder chasing a peer's commit."""
        n = node('EV', owns=['a.py'])
        n['event'] = 'a peer session commits its staged vault files'
        p = gl.plan({'rows': [n]}, slots=3)
        why = dict((x['id'], w) for x, w in p['deferred'])
        self.assertNotIn('FOUNDER-GATED', why['EV'])

    def test_a_node_DEPENDING_on_an_event_node_is_blocked_by_it(self):
        ev = node('EV', owns=['a.py'])
        ev['event'] = 'a peer commits'
        work = node('W', deps=['EV'], owns=['b.py'])
        p = gl.plan({'rows': [ev, work]}, slots=3)
        blocked = dict((x['id'], u) for x, u in p['blocked'])
        self.assertEqual(blocked.get('W'), ['EV'])

    def test_a_node_with_NO_event_is_unaffected(self):
        """The field is optional; adding it must not change any existing node."""
        p = gl.plan({'rows': [node('A', owns=['a.py'])]}, slots=3)
        self.assertEqual([b['id'] for b in p['batch']], ['A'])

    def test_a_SUPERSEDED_node_is_never_dispatched_and_satisfies_its_edge(self):
        """2026-08-30: the ready set offered R12, a SUPERSEDED row whose hours
        had moved into named successors. Superseded work is never claimed, and
        a node depending on it is not held hostage by a row that will never
        run."""
        sup = node('S', owns=['s.py'])
        sup['status'] = 'SUPERSEDED'
        work = node('W', deps=['S'], owns=['w.py'])
        p = gl.plan({'rows': [sup, work]}, slots=3)
        self.assertEqual([b['id'] for b in p['batch']], ['W'])
        self.assertNotIn('S', [b['id'] for b in p['batch']])


if __name__ == '__main__':
    unittest.main()
