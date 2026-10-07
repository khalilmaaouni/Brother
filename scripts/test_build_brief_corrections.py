#!/usr/bin/env python3
"""The brief must contradict its own specification wherever the tree disagrees.

WHAT IT PROTECTS, measured 2026-09-21. A diagnostician round over 13 stuck sub units returned 9 proven facts,
and the same shape dominated: the specification asserts something is NEW and it already exists. Three verbatim:
"scripts/loop_bridge.py already defines run_node, so D1.5's spec's claim that run_node is NEW is false";
"test_dream_world.py already exists with the TestSchema tests"; "dream_grade.py already contains the D9.d
_classify implementation". The brief listed which files existed, but the spec text inside the same brief still
said create it, so the builder had two contradictory instructions, spent its rounds on both, and exhausted. The
loop then recorded that as a missing fact, which it never was.

This is a UNIT test of the corrections function, deliberately, rather than an end to end brief render: the
render needs a dispatcher sized prompt and a real spec tree, and a test that heavy would not be run."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load():
    """build_brief.py runs its work at import, so the function is lifted out by source rather than imported.
    Said plainly rather than dressed up: this is weaker than importing it, and it is why the assertion below
    also pins the call site, so deleting the call cannot leave this test green."""
    src = open(os.path.join(HERE, "loop", "build_brief.py"), encoding="utf-8").read()
    start = src.index("def stale_claims(")
    end = src.index("corrections = stale_claims(")
    sys.path.insert(0, os.path.join(HERE, "loop")); import brief_check
    ns = {"os": os, "re": __import__("re"), "subprocess": __import__("subprocess"),
          "labelled_paths": getattr(brief_check, "labelled_paths", None)}   # FX-09.2: stale_claims reads labels through it
    exec(compile(src[start:end], "build_brief_fragment", "exec"), ns)
    return ns["stale_claims"], src


class TheTreeWinsOverTheSpecification(unittest.TestCase):
    def setUp(self):
        self.stale_claims, self.src = _load()

    def test_a_file_called_new_that_already_exists_is_corrected(self):
        """The exact 2026-09-21 shape: the spec says NEW, the file is on disk."""
        section = "Create the NEW module `scripts/build_brief_probe_target.py` for this sub unit."
        real = "scripts/throughput.py"
        section = section.replace("scripts/build_brief_probe_target.py", real)
        out = self.stale_claims(section, [real])
        self.assertTrue(out, "a file marked NEW that exists on disk must be corrected")
        self.assertIn(real, out[0])
        self.assertIn("ALREADY EXISTS", out[0])

    def test_a_file_correctly_marked_new_says_nothing(self):
        """No false corrections: a genuinely absent file must not be reported as present."""
        section = "Create the NEW module `scripts/this_path_does_not_exist_zz.py`."
        self.assertEqual(self.stale_claims(section, []), [])

    def test_an_existing_file_not_called_new_says_nothing(self):
        """Every existing file is shown to the builder anyway; only the CONTRADICTION is worth a correction."""
        section = "Patch the existing module `scripts/throughput.py` to add a flag."
        self.assertEqual(self.stale_claims(section, ["scripts/throughput.py"]), [])

    def test_a_symbol_called_new_that_is_already_defined_is_corrected(self):
        """`measure(` is defined in scripts/throughput.py, so a spec introducing it as new is stale."""
        out = self.stale_claims("This sub unit adds `measure(` to the module.", [])
        self.assertTrue(out, "a symbol introduced as new that is already defined must be corrected")
        self.assertIn("ALREADY DEFINED", out[0])

    def test_an_unreadable_input_yields_no_false_correction(self):
        self.assertEqual(self.stale_claims("", []), [])

    def test_the_corrections_are_actually_used_by_the_brief(self):
        """Pins the CALL SITE, not only the function. A correction computed and never written into the brief
        would leave every other test here green while changing nothing for any builder."""
        self.assertIn("corrections = stale_claims(section, existing)", self.src)
        self.assertIn("WHERE THE SPECIFICATION IS STALE", self.src)
        self.assertIn("+ corr +", self.src.replace(" ", "").replace("+corr+", " + corr + "))




def _load_shares():
    """Same source lift as _load above, and the same honest caveat: build_brief.py works at import."""
    src = open(os.path.join(HERE, "loop", "build_brief.py"), encoding="utf-8").read()
    start = src.index("def shares(")
    end = src.index("PER = shares(")
    ns = {"os": os}
    exec(compile(src[start:end], "build_brief_shares", "exec"), ns)
    return ns["shares"], src


class TheBriefBudgetGoesWhereItIsNeeded(unittest.TestCase):
    """Measured 2026-09-21 over 40 consecutive grades: 37 FAIL, 3 PASS, dominant verdict RED-WITHOUT-CODE, and
    36 of the 37 failures named unshown or sliced out source in their own UNKNOWNS. An even split gave every
    named file budget // n, so the one large module the sub unit had to test was truncated and the builder could
    not write a test that fails without its code, which is hard rule 4 of its own brief."""

    def setUp(self):
        self.shares, self.src = _load_shares()
        self.big = os.path.join(HERE, "loop", "build_brief.py")      # a real, larger file
        self.small = os.path.join(HERE, "throughput.py")             # a real, smaller file

    def _tree(self, sizes):
        """Real files of chosen sizes, because shares() asks the filesystem and a mock would prove nothing."""
        d = tempfile.mkdtemp()
        paths = []
        for i, n in enumerate(sizes):
            q = os.path.join(d, "f%d.py" % i)
            with open(q, "w", encoding="utf-8") as fh:
                fh.write("x" * n)
            paths.append(q)
        self.addCleanup(shutil.rmtree, d, True)
        return paths

    def test_the_module_under_test_is_not_starved_when_the_budget_is_SCARCE(self):
        """THE CASE THAT FAILED IN PRODUCTION, and the regime that matters.

        When spare budget exists, redistribution hands the leftover to whoever is still short, so an even split
        and a proportional one converge and neither is starved. The difference appears only when total need
        EXCEEDS the budget, which is precisely the production case: a sub unit naming one large shared module
        beside several mid sized ones, all of them truncated. There the even split gives the large module the
        same slice as every sibling, and a builder cannot write a test that fails without code it cannot see.

        Written to fail against the even split's OWN number, so restoring that split turns this red. The first
        version of this test used a spare rich case and passed under BOTH allocations, proving nothing."""
        big, *mid = self._tree([300000, 30000, 30000, 30000])
        budget = 60000
        got = self.shares([big] + mid, budget)
        equal_slice = budget // 4
        self.assertGreater(got[big], equal_slice * 1.3,
                           "under a scarce budget the module under test must outrank its siblings, not tie them")
        self.assertGreater(got[big], max(got[q] for q in mid),
                           "the largest need must take the largest share when nothing can be whole")

    def test_the_allocation_always_fits_the_budget(self):
        """An oversized brief is refused by the dispatcher and comes back WITHHELD, which is one of the blocked
        states this change exists to remove. Many files whose floors would sum past the budget is the real case."""
        for sizes, budget in ([[300000] * 10, 100000], [[50000] * 3, 40000], [[1000] * 4, 10000],
                              [[300000, 4000, 4000, 4000, 4000], 120000]):
            paths = self._tree(sizes)
            got = self.shares(paths, budget)
            self.assertLessEqual(sum(got.values()), budget,
                                 "sizes %s at budget %d overflowed the brief" % (sizes, budget))
            for q in paths:
                self.assertGreaterEqual(got[q], 1, "no named file is dropped to nothing")

    def test_no_named_file_is_TRUNCATED_below_the_floor(self):
        """The floor protects against TRUNCATION, not against being short. A file smaller than the floor is
        included whole, which is complete rather than starved: asserting a flat 20000 for everything was this
        test's own first mistake, and it failed on a real 11 kB file that was fully present."""
        got = self.shares([self.big, self.small], 40000, floor=20000)
        for path, n in got.items():
            whole = os.path.getsize(path)
            self.assertGreaterEqual(n, min(20000, whole),
                                    "a truncated share below the floor is a stub, not an excerpt")
            self.assertLessEqual(n, max(whole, 20000) + 1, "no file is padded past its own size")

    def test_floors_that_fit_the_budget_are_never_scaled(self):
        """Fixed sizes, never a real file that grows: a 33859 and a 6289 byte file under 40000 once cut the small
        one to 6285 because a floor pushed the proportional sum past the budget and everything was scaled."""
        big, small = self._tree([33859, 6289])
        got = self.shares([big, small], 40000, floor=20000)
        self.assertEqual(got[small], 6289, "the small file is whole")
        self.assertGreaterEqual(got[big], 20000)
        self.assertLessEqual(sum(got.values()), 40000)

    def test_a_file_smaller_than_its_share_does_not_hoard_the_budget(self):
        """The leftover must return to the pool; padding a small file is budget burned for nothing."""
        got = self.shares([self.big, self.small], 400000)
        self.assertLessEqual(got[self.small], max(os.path.getsize(self.small), 20000) + 1)

    def test_an_unreadable_size_neither_starves_nor_favours(self):
        got = self.shares(["/no/such/file/at/all.py", self.big], 200000)
        self.assertEqual(got["/no/such/file/at/all.py"], 20000)

    def test_the_allocation_is_actually_used(self):
        """Pins the call site: an allocation computed and never applied changes nothing for any builder."""
        self.assertIn("PER = shares(existing, budget, secondary=neighbours)", self.src)
        self.assertIn("per = PER.get(p, 20000)", self.src)
        self.assertIn("neighbours = set(paths_in.neighbours)", self.src)

    def test_neighbour_suites_never_starve_the_files_the_section_names(self):
        """MEASURED on run 2 of 2026-09-24, D1.8: 8 named files plus 14 neighbour suites, every floor scaled down
        together, the sub unit's own 5716 byte test kept 265 bytes and two named modules read NOT SHOWN. A
        neighbour exists so an edit keeps it green; it never outranks the file the edit is made to."""
        named = self._tree([100000, 6000])
        neigh = self._tree([20000] * 10)
        budget = 120000
        got = self.shares(named + neigh, budget, secondary=neigh)
        self.assertLessEqual(sum(got.values()), budget)
        self.assertEqual(got[named[1]], 6000, "the section's own small file must be whole")
        self.assertGreaterEqual(got[named[0]], 100000, "the section's large file must be whole before any neighbour")
        self.assertLessEqual(sum(got[q] for q in neigh), budget - 106000)
        # and with nothing named as secondary the old behaviour is untouched
        self.assertEqual(self.shares(named, budget), self.shares(named, budget, secondary=()))


def _load_paths_in():
    src = open(os.path.join(HERE, "loop", "build_brief.py"), encoding="utf-8").read()
    start = src.index("def paths_in(")
    end = src.index("paths = paths_in(section)")
    sys.path.insert(0, os.path.join(HERE, "loop")); import brief_check
    ns = {"os": os, "re": __import__("re"), "SPEC_PATH": brief_check.SPEC_PATH}
    exec(compile(src[start:end], "build_brief_paths", "exec"), ns)
    return ns["paths_in"], src


class ABriefIsNeverDispatchedBlind(unittest.TestCase):
    """Measured 2026-09-21 over four live briefs: two carried 49 and 969 bytes of real source, which is none,
    against a 196500 budget nowhere near spent. D9.d's entire section reads "Exact files: same two NEW files",
    naming its files by pointing at a sibling section, so the path regex found nothing and the builder was handed
    ZERO source while still being told its tests must fail without its code. Its done check named the module all
    along and nothing read it."""

    def setUp(self):
        """Its OWN fixture, not this repository's modules. The first version named real files under
        plugin/runtime/brother/core/, which do not ship in the public export tree, and the pre-push hermetic
        gate refused it: green here, red where the public runner runs it. That is the exact class that gate
        exists to catch, so the fixture is built rather than borrowed."""
        self.paths_in, self.src = _load_paths_in()
        self.tmp = tempfile.mkdtemp()
        pkg = os.path.join(self.tmp, "pkgx", "sub")
        os.makedirs(pkg)
        for name in ("test_widget.py", "widget.py"):
            with open(os.path.join(pkg, name), "w", encoding="utf-8") as fh:
                fh.write("# fixture\n")
        self._cwd = os.getcwd()
        os.chdir(self.tmp)                      # paths_in resolves relative paths against the working directory
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.chdir, self._cwd)

    def test_a_dotted_done_check_recovers_the_test_module(self):
        got = self.paths_in("Done-check: `python3 -B -m unittest pkgx.sub.test_widget.TestComparator`")
        self.assertIn(os.path.join("pkgx", "sub", "test_widget.py"), got)

    def test_it_also_recovers_the_module_under_test_beside_it(self):
        """The estate's own convention: the module sits beside its test. That is the file a builder must read to
        write a test that fails without its code."""
        got = self.paths_in("python3 -B -m unittest pkgx.sub.test_widget.TestComparator")
        self.assertIn(os.path.join("pkgx", "sub", "widget.py"), got)

    def test_a_trailing_class_and_method_are_trimmed_until_a_file_appears(self):
        got = self.paths_in("unittest pkgx.sub.test_widget.TestX.test_y")
        self.assertIn(os.path.join("pkgx", "sub", "test_widget.py"), got)

    def test_a_label_inside_the_backtick_does_not_break_the_match(self):
        """MEASURED ON L0.1, whose 2981 byte section named SIX real paths and yielded ZERO. Every one was
        written as `NEW: docs/plan/L0-WAIVERS.json` or `existing: scripts/l0_gate.py`. The pattern anchored the
        path to the backticks, so the label made it match nothing and the builder was handed no source. Third
        distinct shape of the same starvation, after a section naming files by pointing at a sibling and a
        section naming only a done check."""
        got = self.paths_in("Files:\n- `NEW: pkgx/sub/widget.py`\n- `existing: pkgx/sub/test_widget.py`")
        self.assertIn("pkgx/sub/widget.py", got)
        self.assertIn("pkgx/sub/test_widget.py", got)

    def test_an_unlabelled_backticked_path_still_matches(self):
        self.assertIn("pkgx/sub/widget.py", self.paths_in("edit `pkgx/sub/widget.py`"))

    def test_a_dotted_name_resolving_to_no_file_is_dropped_never_guessed(self):
        self.assertEqual(self.paths_in("see a.b.c.d.e for details"), [])

    def test_backticked_paths_still_win_and_specs_are_excluded(self):
        got = self.paths_in("edit `pkgx/sub/widget.py` per `docs/plan/specs/D9.md`")
        self.assertIn("pkgx/sub/widget.py", got)
        self.assertNotIn("docs/plan/specs/D9.md", got)

    def test_the_whole_spec_fallback_is_wired(self):
        """Pins the call site: recovering paths and never widening on a section that names none would leave the
        starved case exactly as it was."""
        self.assertIn("paths = paths_in(section)", self.src)
        self.assertIn("for q in paths_in(spec):", self.src)


class NeighbourSuites(unittest.TestCase):
    """paths_in, extracted the same way: a module named by the spec brings its test_<module>*.py neighbours, a test brings
    its module (2026-09-24: workers broke existing suites they were never shown)."""
    def paths_in(self, text, tree):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "build_brief.py"), encoding="utf-8").read()
        body = src.split("\ndef paths_in(text):", 1)[1].split("\n\n\npaths = paths_in(section)", 1)[0]
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")); import brief_check
        ns = {"os": os, "re": __import__("re"), "glob": __import__("glob"), "SPEC_PATH": brief_check.SPEC_PATH}
        exec("def paths_in(text):" + body, ns)
        cwd = os.getcwd(); os.chdir(tree)
        try: return ns["paths_in"](text)
        finally: os.chdir(cwd)
    def test_a_path_labelled_after_it_inside_the_backticks_is_still_shown(self):
        d = tempfile.mkdtemp(prefix="brief-label-"); os.makedirs(os.path.join(d, "pkg"))
        open(os.path.join(d, "pkg", "loop_bridge.py"), "w").write("x = 1\n")
        got = self.paths_in("- `pkg/loop_bridge.py (existing)` dispatchable\n- `pkg/test_new.py (NEW)` defines tests\n- `logs/g.jsonl`", d)
        self.assertIn("pkg/loop_bridge.py", got); self.assertIn("pkg/test_new.py", got); self.assertNotIn("logs/g.jsonl", got)

    def test_a_module_brings_its_existing_suites_and_a_test_brings_its_module(self):
        d = tempfile.mkdtemp(prefix="brief-neigh-"); os.makedirs(os.path.join(d, "pkg"))
        for n in ("m.py", "test_m.py", "test_m_extra.py", "other.py", "test_other.py"): open(os.path.join(d, "pkg", n), "w").write("x = 1\n")
        got = self.paths_in("Edit `pkg/m.py` and read pkg.test_other.T", d)
        self.assertIn("pkg/test_m.py", got); self.assertIn("pkg/test_m_extra.py", got); self.assertIn("pkg/other.py", got)
        self.assertNotIn("pkg/test_nothing.py", got)

    def test_only_suites_are_neighbours_the_module_a_named_test_brings_ranks_first(self):
        """R4.2, 2026-09-24: jev_seam.py came in as test_jev_seam.py's sibling, was ranked as a neighbour, and got a
        2 byte share of the brief while the sub unit exists to test it."""
        d = tempfile.mkdtemp(prefix="brief-neigh2-"); os.makedirs(os.path.join(d, "pkg"))
        for n in ("m.py", "test_m.py", "test_m_extra.py"): open(os.path.join(d, "pkg", n), "w").write("x = 1\n")
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "build_brief.py"), encoding="utf-8").read()
        body = src.split("\ndef paths_in(text):", 1)[1].split("\n\n\npaths = paths_in(section)", 1)[0]
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")); import brief_check
        ns = {"os": os, "re": __import__("re"), "glob": __import__("glob"), "SPEC_PATH": brief_check.SPEC_PATH}
        exec("def paths_in(text):" + body, ns)
        cwd = os.getcwd(); os.chdir(d)
        try:
            got = ns["paths_in"]("Extend `pkg/test_m.py`"); neigh = list(ns["paths_in"].neighbours)
        finally: os.chdir(cwd)
        self.assertIn("pkg/m.py", got); self.assertNotIn("pkg/m.py", neigh)
        got2 = None
        os.chdir(d)
        try:
            got2 = ns["paths_in"]("Edit `pkg/m.py`"); neigh2 = list(ns["paths_in"].neighbours)
        finally: os.chdir(cwd)
        self.assertIn("pkg/test_m_extra.py", neigh2); self.assertIn("pkg/test_m.py", got2)

    def test_a_named_test_brings_the_real_modules_it_imports_wherever_they_sit(self):
        """MEASURED on run 2 of 2026-09-24: D3.7's section names only plugin/.../test_dream_seams.py, which imports
        jev_seam from scripts/; no dream_seams.py sits beside it, so the brief carried zero source and eleven builds
        guessed. A bare import resolves under scripts/ (where the plugin tests insert into sys.path), a dotted one
        as a package path, a sibling beside the test; stdlib and unknown names resolve to nothing and are dropped."""
        d = tempfile.mkdtemp(prefix="brief-imports-"); os.makedirs(os.path.join(d, "pkg")); os.makedirs(os.path.join(d, "scripts"))
        for n in ("scripts/util.py", "scripts/helper.py", "pkg/deep.py", "pkg/near.py"): open(os.path.join(d, n), "w").write("x = 1\n")
        open(os.path.join(d, "pkg", "test_thing.py"), "w").write(
            "import os\nimport helper\nimport scripts.util as util\nfrom pkg.deep import x\nimport near, ghost\n")
        got = self.paths_in("Extend `pkg/test_thing.py`", d)
        for want in ("scripts/helper.py", "scripts/util.py", "pkg/deep.py", "pkg/near.py"): self.assertIn(want, got)
        self.assertFalse([g for g in got if "ghost" in g or g.endswith("os.py")], got)
        self.assertEqual(got.count("pkg/test_thing.py"), 1)

    def test_a_done_checks_dependent_suites_are_neighbours_never_followed(self):
        """MEASURED 2026-09-30 on the L5a round 3 specs: a done check naming its own test and then the suites the
        change must keep green made every dependent a named test, their imports rode along as named files, and the
        L5a-8c brief sliced out run_wave and _record_choice, which it owns. A module after the first of a
        `-m unittest` command is a neighbour and nothing more; one the section also names in backticks stays named."""
        d = tempfile.mkdtemp(prefix="brief-deps-"); os.makedirs(os.path.join(d, "pk", "sub"))
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("own.py", "dep.py", "heavy.py"): open(os.path.join(d, "pk", "sub", n), "w").write("x = 1\n")
        open(os.path.join(d, "pk", "sub", "test_dep.py"), "w").write("from pk.sub.heavy import x\n")
        src = open(os.path.join(HERE, "loop", "build_brief.py"), encoding="utf-8").read()
        body = src.split("\ndef paths_in(text):", 1)[1].split("\n\n\npaths = paths_in(section)", 1)[0]
        sys.path.insert(0, os.path.join(HERE, "loop")); import brief_check
        ns = {"os": os, "re": __import__("re"), "glob": __import__("glob"), "SPEC_PATH": brief_check.SPEC_PATH}
        exec("def paths_in(text):" + body, ns)
        check = "Done check:\n\n```\npython3 -B -m unittest pk.sub.test_own pk.sub.test_dep\n```\n"
        cwd = os.getcwd(); os.chdir(d)
        try:
            got = ns["paths_in"]("Edit `pk/sub/own.py`.\n\n" + check); neigh = list(ns["paths_in"].neighbours)
            got2 = ns["paths_in"]("Edit `pk/sub/own.py` and `pk/sub/test_dep.py`.\n\n" + check); neigh2 = list(ns["paths_in"].neighbours)
        finally: os.chdir(cwd)
        self.assertIn("pk/sub/test_dep.py", got); self.assertIn("pk/sub/test_dep.py", neigh)
        self.assertNotIn("pk/sub/heavy.py", got); self.assertNotIn("pk/sub/dep.py", got)
        self.assertIn("pk/sub/own.py", got); self.assertNotIn("pk/sub/own.py", neigh)
        self.assertNotIn("pk/sub/test_dep.py", neigh2); self.assertIn("pk/sub/heavy.py", got2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
