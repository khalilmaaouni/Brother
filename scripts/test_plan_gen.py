#!/usr/bin/env python3
"""PG1.b: tests for scripts/plan_gen.py, the one command that builds the next release plan from a seed.

Every case builds its own git repository in a temporary directory (a tracked spec, a tracked plan, a template
generator, source files for the cached reads), builds the seed with plan_seed.build_seed and drives the real entry
point plan_gen.main as well as the functions. HOME is an empty temporary directory for the whole run; no case needs
ps, gh, the network or the real docs/plan. The cases that read the real 1.1.4 files are skipped on a tree without
docs/plan/specs. No em or en dashes and no clock text are typed in this file: such strings are built in code.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plan_gen as PG  # noqa: E402
import plan_seed as PS  # noqa: E402

NOW = 1791990000.0
VERSION = "9.9.10"
NEXT = "9.9.11"  # the release after the closing plan of the carried cases
CLOSING = "docs/plan/BROTHER-%s-WBS.json" % VERSION
HEADS = ["## %d. Heading %d" % (i, i) for i in range(1, 12)]
FILLS = ["WRITER: fill %d" % i for i in range(1, 12)]
EM = chr(0x2014)
WBS_KEYS = ["schema", "initiative", "eyebrow", "as_of", "version_of_plan", "north_star", "the_journey", "epics", "sources",
            "glance", "cut_band", "calendar", "waves", "budget", "units", "deferred", "decisions_waiting", "risks"]
UNIT_KEYS = ["id", "family", "epic", "wave", "lane", "plan_start", "plan_end", "size", "size_hours", "owner_hand", "title",
             "objective", "user_outcome", "state", "owns", "sub_units", "worker", "checker", "reads", "done_check", "after",
             "depends_on", "source", "remains", "spec"]
CARRIED_KEYS = ("carried", "sub_units", "worker", "spec", "done_check", "checker", "from_plan")
HUB = os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs"))


def unit(uid, **kw):
    low = uid.lower()
    u = {"id": uid, "title": "title " + uid, "objective": "objective " + uid, "state": "DEFERRED",
         "owns": ["scripts/%s.py" % low, "scripts/test_%s.py" % low], "after": [], "source": "src " + uid, "reads": [],
         "remains": "everything", "spec": "docs/plan/specs/%s.md" % uid}
    u.update(kw)
    return u


def git(root, *args):
    subprocess.run(("git", "-C", root) + args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def listing(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        out += [os.path.relpath(os.path.join(base, n), root) for n in dirs + files]
    return sorted(out)


class Fix:
    """A fixture repository: tracked spec TAKEN1, tracked plan holding OLD1, a template, a source file for reads."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg1b_test_")
        self._env = {k: os.environ.get(k) for k in ("HOME", "GIT_CEILING_DIRECTORIES")}
        os.environ["HOME"] = os.path.join(self.tmp, "emptyhome")  # an empty HOME: nothing may lean on this laptop
        os.makedirs(os.environ["HOME"])
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs", "9.9.9"))
        os.makedirs(os.path.join(self.root, "scripts"))
        self.put("docs/plan/specs/TAKEN1.md", "# TAKEN1\n")
        self.put("docs/plan/BROTHER-9.9.9-WBS.json", json.dumps({"units": [{"id": "OLD1"}]}))
        self.put("docs/plan/specs/9.9.9/pack_v999.py", "HEADINGS = %r\nFILL = %r\n" % (HEADS, FILLS))
        self.put("scripts/code.py", "\n".join("line %d" % i for i in range(1, 101)) + "\n")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        self.seed_path = os.path.join(self.tmp, "seed.json")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def make_seed(self, units, edit=None, version=VERSION, plan=None, from_plan=None):
        """A seed built the way the ceremony builds one. The parked rows come out CARRIED; unless `from_plan` names a
        closing plan they are edited back to fresh planner rows, the shape the cases before PG1.b's seam fix used."""
        plan = plan or {"schema": "brother-release-wbs/1", "as_of": "2026-10-14 (fixture)", "units": list(units)}
        seed = PS.build_seed(version, plan, None, None, [], {"model": "m words", "budget": "b words", "deadline": "d words"},
                             NOW, self.root, from_plan=from_plan)
        if from_plan is None:
            for u in seed["units"]:
                for key in CARRIED_KEYS:
                    u.pop(key, None)
        if edit:
            edit(seed)
        with open(self.seed_path, "w", encoding="utf-8") as f:
            json.dump(seed, f)
        return seed

    def run_gen(self, extra=None, root=None, version=VERSION):
        out = io.StringIO()
        argv = ["--seed", self.seed_path, "--version", version, "--root", root or self.root] + (extra or [])
        with contextlib.redirect_stdout(out):
            code = PG.main(argv)
        return code, out.getvalue()

    def rendered(self, units, **kw):
        seed = self.make_seed(units)
        return PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS), **kw)


class TestCollisions(Fix, unittest.TestCase):
    def test_a_colliding_id_refuses_before_any_write(self):
        for clash in ("TAKEN1", "OLD1"):  # a tracked spec, and a unit of a tracked plan
            self.make_seed([unit("AA1"), unit(clash)])
            before = listing(self.root)
            code, out = self.run_gen()
            self.assertEqual(code, 1, out)
            self.assertIn(clash, out)
            self.assertEqual(listing(self.root), before, "nothing may be written")

    def test_the_clash_is_named_even_when_another_row_has_no_owns(self):
        self.make_seed([unit("TAKEN1"), unit("AA1")], edit=lambda s: s["units"][1].update(owns=[]))
        code, out = self.run_gen()
        self.assertEqual(code, 1, out)
        self.assertIn("already tracked under docs/plan: TAKEN1", out)

    def test_collisions_names_every_clash_sorted_and_a_missing_id(self):
        self.assertEqual(PG.collisions([{"id": "B"}, {"id": "A"}, {"id": "C"}], {"A", "B"}), ["A", "B"])
        self.assertEqual(PG.collisions([{"title": "x"}, {"id": 7}, {"id": ""}], set()), ["<no id>"])
        self.assertEqual(PG.collisions([], {"A"}), [])

    def test_taken_ids_reads_the_index_not_the_disk(self):
        self.assertEqual(PG.taken_ids(self.root), {"TAKEN1", "OLD1"})
        self.put("docs/plan/specs/UNTRACKED.md", "# x\n")  # on disk but not in the index
        self.assertNotIn("UNTRACKED", PG.taken_ids(self.root))

    def test_git_unreadable_or_a_bad_seed_refuses(self):
        bare = os.path.join(self.tmp, "norepo")
        os.makedirs(bare)
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp  # no parent repository may answer for it
        with self.assertRaises(RuntimeError):
            PG.taken_ids(bare)
        self.make_seed([unit("AA1")])
        code, out = self.run_gen(root=bare)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertEqual(listing(bare), [])
        self.put("docs/plan/BROTHER-9.9.8-WBS.json", "{not json")  # a tracked plan that cannot be read is NO-DATA too
        git(self.root, "add", "-A")
        with self.assertRaises(RuntimeError):
            PG.taken_ids(self.root)

    def test_a_bad_seed_or_version_is_a_refusal_that_writes_nothing(self):
        before = listing(self.root)
        self.make_seed([unit("AA1")], edit=lambda s: s.update(schema="brother-plan-seed/0"))
        self.assertEqual(self.run_gen()[0], 1)
        self.make_seed([unit("AA1")], version="9.9.11")  # a seed written for another release
        self.assertEqual(self.run_gen()[0], 1)
        self.make_seed([unit("AA1")])
        for bad in ("1.1", "v1.1.5", "1.1.5.1", "1.1.5-rc1"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(PG.main(["--seed", self.seed_path, "--version", bad, "--root", self.root]), 1, bad)
        with open(self.seed_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.run_gen()[0], 1)
        os.remove(self.seed_path)
        self.assertEqual(self.run_gen()[0], 2)  # no seed file at all is NO-DATA
        self.assertEqual(listing(self.root), before)

    def test_the_template_absent_is_no_data_and_writes_nothing(self):
        self.make_seed([unit("AA1")])
        os.remove(os.path.join(self.root, "docs/plan/specs/9.9.9/pack_v999.py"))
        git(self.root, "add", "-A")
        before = listing(self.root)
        code, out = self.run_gen()
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertEqual(listing(self.root), before)


class TestRendering(Fix, unittest.TestCase):
    def test_the_rendering_has_the_wbs_section_and_pack_shapes(self):
        subs = [{"id": "BB2.a", "title": "first"}, {"id": "BB2.b", "title": "second", "files": ["scripts/test_bb2.py"]}]
        seed = self.make_seed([unit("AA1", after=["ZZ9"]), unit("BB2")], edit=lambda s: s["units"][1].update(sub_units=subs))
        r = PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        self.assertEqual(list(r["wbs"].keys()), WBS_KEYS)
        self.assertEqual(r["wbs"]["schema"], "brother-release-wbs/1")
        self.assertEqual([list(u.keys()) for u in r["wbs"]["units"]], [UNIT_KEYS, UNIT_KEYS])
        self.assertEqual(r["wbs"]["units"][1]["sub_units"], ["BB2.a", "BB2.b"])
        self.assertEqual(r["wbs"]["budget"]["deadline"], "d words")
        spec = r["specs"]["docs/plan/specs/BB2.md"]
        for h in ("## 1. Measured baseline", "## 2. Sub-units", "### BB2.a first", "### BB2.b second", "#### Edges and failures",
                  "## 3. Parent close and evidence"):
            self.assertIn(h, spec)
        self.assertIn("`scripts/test_bb2.py` (NEW)", spec)
        self.assertIn("Done check: `python3 -B scripts/test_bb2.py`", spec)
        packs = [t for p, t in r["pack"].items() if p.endswith(".md")]
        self.assertEqual(len(packs), 3)
        for text in packs:
            self.assertEqual([l for l in text.split("\n") if l.startswith("## ")], HEADS)
        batches = r["assignments"]["batches"]
        self.assertEqual([b["id"] for b in batches], ["AA1.a", "BB2.a", "BB2.b"])  # one batch per sub unit
        manifest = json.loads(r["pack"]["docs/plan/specs/%s/pack/manifest.json" % VERSION])
        self.assertEqual({s["id"]: s["deps"] for s in manifest["subunits"]}, {"AA1.a": [], "BB2.a": [], "BB2.b": ["BB2.a"]})

    def test_a_clock_time_in_the_grant_words_is_written_as_hours_h_minutes_and_the_run_is_not_refused(self):
        clock, other = "18" + ":" + "30", "07" + ":" + "00"
        seed = self.make_seed([unit("AA1")], edit=lambda s: s["grant"].update(deadline=clock, model="m until %s JST" % other))
        r = PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        self.assertEqual(r["wbs"]["budget"]["deadline"], "18h30")
        self.assertEqual(r["wbs"]["budget"]["model"], "m until 07h00 JST")
        self.assertEqual(r["assignments"]["model"], "m until 07h00 JST")
        code, out = self.run_gen()
        self.assertEqual(code, 0, out)
        self.assertEqual(seed["grant"]["deadline"], clock, "the seed keeps the owner's words as given")

    def test_the_same_seed_renders_the_same_bytes(self):
        units = [unit("AA1"), unit("BB2")]
        self.assertEqual(PG._files(self.rendered(units)), PG._files(self.rendered(units)))

    def test_cached_reads_are_pasted_into_the_stub(self):
        r = self.rendered([unit("AA1", reads=["scripts/code.py helper (line 5)", "scripts/gone.py thing (line 1)"])])
        spec = r["specs"]["docs/plan/specs/AA1.md"]
        self.assertIn("scripts/code.py line 5 sha256 ", spec)
        self.assertIn("    line 5\n    line 6", spec)
        self.assertIn("    scripts/gone.py line 1\n    NO-DATA: ", spec)
        batch = r["assignments"]["batches"][0]
        self.assertEqual([c["path"] for c in batch["cached_reads"]], ["scripts/code.py", "scripts/gone.py"])
        self.assertIn("scripts/code.py helper (line 5)", batch["reads"])

    def test_a_huge_cached_read_is_cut_to_forty_lines_and_marked(self):
        seed = self.make_seed([unit("AA1", reads=["scripts/code.py x (line 1)"])])
        seed["units"][0]["reads_cache"] = [{"path": "big.py", "line": 1, "text": "\n".join("L%d" % i for i in range(5000))}]
        r = PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        spec = r["specs"]["docs/plan/specs/AA1.md"]
        self.assertIn("    big.py line 1 (truncated)", spec)
        self.assertIn("    L39\n", spec)
        self.assertNotIn("L40\n", spec)

    def test_an_empty_or_hostile_unit_row_is_refused_by_name(self):
        owns = ["scripts/x.py", "scripts/test_x.py"]
        for bad, why in ((unit("AA1", owns=[]), "no owns"), (unit("../X", owns=owns), "plain id"), (unit("A B", owns=owns), "plain id"),
                         (unit("AA1", owns=["scripts/plain.py"]), "no done check")):
            seed = self.make_seed([bad])
            with self.assertRaisesRegex(ValueError, why, msg=bad["id"]):
                PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        seed = self.make_seed([unit("AA1")], edit=lambda s: s["units"][0].update(owns="scripts/a.py"))  # a string, not a list
        with self.assertRaises(ValueError):
            PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        seed = self.make_seed([unit("AA1")], edit=lambda s: s["units"][0].update(sub_units=[{"id": "OTHER.a"}]))
        with self.assertRaises(ValueError):
            PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        seed = self.make_seed([unit("AA1"), unit("AA1")])
        with self.assertRaises(ValueError):
            PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))

    def test_a_row_without_after_gets_an_empty_list_and_a_loop_worker(self):
        seed = self.make_seed([unit("AA1")])
        del seed["units"][0]["after"]
        r = PG.render_plan(seed, VERSION, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        self.assertEqual(r["wbs"]["units"][0]["after"], [])
        self.assertTrue(r["wbs"]["units"][0]["worker"].startswith("Brother.Loop"))
        self.assertEqual(r["wbs"]["units"][0]["state"], "DEFERRED")

    def test_no_template_under_the_root_is_no_data(self):
        with self.assertRaisesRegex(RuntimeError, "NO-DATA: no docs/plan/specs"):
            PG.find_template(os.path.join(self.tmp, "emptyhome"))
        self.put("docs/plan/specs/9.9.11/pack_v9911.py", "HEADINGS = ['a']\nFILL = ['b']\n")  # the newest version wins
        self.assertTrue(PG.find_template(self.root).endswith("9.9.11/pack_v9911.py"))

    def test_a_template_without_headings_is_no_data(self):
        path = self.put("docs/plan/specs/9.9.9/pack_vbad.py", "HEADINGS = ['a']\nFILL = []\n")
        with self.assertRaises(RuntimeError):
            PG.load_template(path)
        with self.assertRaises(RuntimeError):
            PG.load_template(os.path.join(self.tmp, "absent.py"))
        self.assertEqual(PG.load_template(os.path.join(self.root, "docs/plan/specs/9.9.9/pack_v999.py")), (HEADS, FILLS))

    @unittest.skipUnless(HUB, "docs/plan/specs is hub only")
    def test_the_real_1_1_4_files_have_the_same_shapes(self):
        with open(os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.4-WBS.json"), encoding="utf-8") as f:
            real = json.load(f)
        self.assertEqual(list(real.keys()), WBS_KEYS)
        self.assertEqual([k for k in real["units"][-1].keys() if k in UNIT_KEYS], UNIT_KEYS)
        heads, fills = PG.load_template(PG.find_template(ROOT))
        self.assertEqual(len(heads), 11)
        self.assertEqual(len(fills), 11)


class TestLintAndWrite(Fix, unittest.TestCase):
    def test_a_clean_plan_lints_with_no_finding_and_is_written(self):
        self.make_seed([unit("AA1"), unit("BB2", after=["AA1"])])
        code, out = self.run_gen()
        self.assertEqual(code, 0, out)
        for rel in ("docs/plan/BROTHER-%s-WBS.json" % VERSION, "docs/plan/specs/AA1.md", "docs/plan/specs/BB2.md",
                    "docs/plan/specs/%s/WRITER-ASSIGNMENTS.json" % VERSION, "docs/plan/specs/%s/pack/manifest.json" % VERSION):
            self.assertTrue(os.path.isfile(os.path.join(self.root, rel)), rel)
        self.assertRegex(out, r"PLAN: units 2 sub units 2 batches 2 seconds [0-9.]+\n")

    def test_a_lint_blocking_finding_refuses_the_write(self):
        def loop_builds_the_battery(seed):  # the seed row is edited the way a planner would edit it
            seed["units"][1].update(worker="Brother.Loop (loop seats)", owns=["scripts/check_all.sh", "scripts/test_aa1.py"])

        def names_a_file_it_does_not_own(seed):
            seed["units"][1]["sub_units"] = [{"id": "AA1.a", "files": ["scripts/test_aa1.py", "scripts/stray.py"]}]

        for edit, rule in ((loop_builds_the_battery, "PROTECTED-TOUCH"), (names_a_file_it_does_not_own, "OWNS")):
            self.make_seed([unit("BB2"), unit("AA1")], edit=edit)
            before = listing(self.root)
            code, out = self.run_gen()
            self.assertEqual(code, 1, out)
            self.assertIn(rule, out)
            self.assertEqual(listing(self.root), before, "nothing may be written, not even the clean unit")

    def test_lint_before_write_leaves_no_overlay_and_the_real_tree_untouched(self):
        r = self.rendered([unit("AA1")])
        before = listing(self.root)
        self.assertEqual(PG.lint_before_write(r, self.root), [])
        self.assertEqual(listing(self.root), before)
        left = [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("pg1b_lint_")]
        self.assertEqual(left, [])

    def test_an_empty_seed_renders_a_plan_the_lint_refuses(self):
        self.make_seed([])
        before = listing(self.root)
        code, out = self.run_gen()
        self.assertEqual(code, 1, out)
        self.assertIn("no unit", out)
        self.assertEqual(listing(self.root), before)

    def test_an_existing_target_or_a_dash_refuses_before_writing(self):
        r = self.rendered([unit("AA1"), unit("BB2")])
        self.put("docs/plan/specs/BB2.md", "held\n")  # the second target exists: the first must not be written either
        with self.assertRaises(ValueError) as ctx:
            PG.write_plan(r, self.root)
        self.assertIn("docs/plan/specs/BB2.md exists already", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "docs/plan/specs/AA1.md")))
        with open(os.path.join(self.root, "docs/plan/specs/BB2.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "held\n")

    def test_a_dash_or_clock_text_in_a_rendered_file_refuses(self):
        clock = "at 09" + ":" + "30 sharp"
        for text, word in ((("one %s two" % EM), "dash"), (("one %s two" % chr(0x2013)), "dash"), (clock, "clock")):
            self.make_seed([unit("AA1", objective=text)])
            before = listing(self.root)
            code, out = self.run_gen()
            self.assertEqual(code, 1, out)
            self.assertIn(word, out)
            self.assertEqual(listing(self.root), before)

    def test_a_second_run_winning_the_race_is_refused_not_overwritten(self):
        r = self.rendered([unit("AA1")])
        held = self.put(r["wbs_path"], "held by the other run\n")
        real = os.path.lexists
        PG.os.path.lexists = lambda path: False  # the pre check is blind: only the exclusive create can refuse
        try:
            with self.assertRaisesRegex(ValueError, "appeared while writing"):
                PG.write_plan(r, self.root)
        finally:
            PG.os.path.lexists = real
        with open(held, encoding="utf-8") as f:
            self.assertEqual(f.read(), "held by the other run\n")

    def test_a_lint_that_reads_fewer_sections_than_the_plan_holds_is_no_data(self):
        r = self.rendered([unit("AA1")])
        pl = PG._plan_lint()
        real = pl.lint_plan
        def short(*a, **k):
            rep = real(*a, **k)
            rep["read"] = dict(rep["read"], sections=0)   # a lint that skipped an OPEN section it owed
            return rep
        from unittest import mock
        with mock.patch.object(pl, "lint_plan", short):
            self.assertTrue(any("read 0 of 1" in l for l in PG.lint_before_write(r, self.root)))
        # A LANDED sub unit is skipped by the lint on purpose and is not owed a read (2026-10-14 seam fix).
        r["wbs"]["units"][0]["evidence"] = "AA1.a landed"
        self.assertFalse([l for l in PG.lint_before_write(r, self.root) if "sub unit sections" in l])

    def test_a_path_leaving_the_root_refuses(self):
        r = self.rendered([unit("AA1")])
        r["specs"]["../escape.md"] = "x\n"
        with self.assertRaises(ValueError):
            PG.write_plan(r, self.root)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.md")))

    def test_dry_renders_and_lints_but_writes_nothing(self):
        self.make_seed([unit("AA1")])
        before = listing(self.root)
        code, out = self.run_gen(["--dry"])
        self.assertEqual(code, 0, out)
        self.assertIn("dry, nothing written", out)
        self.assertNotIn("WROTE", out)
        self.assertEqual(listing(self.root), before)

    def test_a_rerun_on_a_written_plan_refuses_on_every_existing_target(self):
        self.make_seed([unit("AA1")])
        self.assertEqual(self.run_gen()[0], 0)
        code, out = self.run_gen()  # untracked: the ids do not collide, the targets do
        self.assertEqual(code, 1, out)
        self.assertIn("docs/plan/BROTHER-%s-WBS.json exists already" % VERSION, out)
        self.assertIn("docs/plan/specs/AA1.md exists already", out)
        git(self.root, "add", "-A")  # tracked now: the id itself collides
        code, out = self.run_gen()
        self.assertEqual(code, 1, out)
        self.assertIn("AA1", out)


class TestCarried(Fix, unittest.TestCase):
    """The seam fix: a CARRIED unit keeps its tracked id and its existing spec; nothing else may."""

    def close_plan(self, units=None, edit=None):
        """Generate and track release 9.9.10 (the closing plan, once); returns its path and its record."""
        if not os.path.exists(os.path.join(self.root, CLOSING)):
            self.make_seed(units or [unit("CARRY1")], edit=edit)
            code, out = self.run_gen()
            self.assertEqual(code, 0, out)
            git(self.root, "add", "-A")
        with open(os.path.join(self.root, CLOSING), encoding="utf-8") as f:
            return CLOSING, json.load(f)

    def carry(self, units=None, edit=None, closing_edit=None, extra=(), from_plan=None):
        """The closing plan, then the seed of the next release built from it; the seed is edited by `edit`."""
        rel, plan = self.close_plan(units, closing_edit)

        def both(seed):
            seed["units"].extend(extra)
            if edit:
                edit(seed)

        return self.make_seed([], edit=both, version=NEXT, plan=plan, from_plan=from_plan or rel)

    def gen_next(self):
        return self.run_gen(version=NEXT)

    def spec_bytes(self, uid="CARRY1"):
        with open(os.path.join(self.root, "docs/plan/specs/%s.md" % uid), "rb") as f:
            return f.read()

    def test_a_carried_unit_keeps_its_id_and_its_spec_and_a_fresh_one_is_generated(self):
        self.carry(extra=[unit("NEW1")])
        held = self.spec_bytes()
        code, out = self.gen_next()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.spec_bytes(), held, "the carried spec is never rewritten")
        with open(os.path.join(self.root, "docs/plan/BROTHER-%s-WBS.json" % NEXT), encoding="utf-8") as f:
            rows = {u["id"]: u for u in json.load(f)["units"]}
        self.assertEqual(rows["CARRY1"]["spec"], "docs/plan/specs/CARRY1.md")
        self.assertEqual(rows["CARRY1"]["sub_units"], ["CARRY1.a"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "docs/plan/specs/NEW1.md")))
        self.assertIn("units 2 sub units 2 batches 1 ", out)  # one batch: the fresh unit; the carried spec is already written

    def test_a_carried_unit_writes_no_spec_pack_or_batch(self):
        seed = self.carry(extra=[unit("NEW1")])
        r = PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        self.assertEqual(list(r["specs"]), ["docs/plan/specs/NEW1.md"])
        self.assertEqual([b["id"] for b in r["assignments"]["batches"]], ["NEW1.a"])
        self.assertFalse([p for p in r["pack"] if "CARRY1" in p])

    def test_a_carried_row_uses_its_own_sub_units_and_worker_not_an_invented_one(self):
        subs = [{"id": "CARRY1.a", "title": "first"}, {"id": "CARRY1.b", "title": "second", "files": ["scripts/test_carry1.py"]}]
        seed = self.carry(closing_edit=lambda s: s["units"][0].update(sub_units=subs, worker="a named worker"),
                          edit=lambda s: s["units"][0].update(checker="a custom checker"))
        self.assertEqual(seed["units"][0]["sub_units"], ["CARRY1.a", "CARRY1.b"])
        r = PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        row = r["wbs"]["units"][0]
        self.assertEqual((row["sub_units"], row["worker"]), (["CARRY1.a", "CARRY1.b"], "a named worker"))
        self.assertEqual((row["done_check"], row["checker"]), (seed["units"][0]["done_check"], "a custom checker"))

    def test_a_carried_row_keeps_its_evidence_and_every_row_depends_on_its_after(self):
        seed = self.carry(extra=[unit("NEW1")], edit=lambda s: (s["units"][0].update(evidence="CARRY1.a landed 2026-10-14: green."),
                                                              s["units"][1].update(after=["CARRY1"])))
        r = PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        rows = {u["id"]: u for u in r["wbs"]["units"]}
        self.assertEqual(rows["CARRY1"]["evidence"], "CARRY1.a landed 2026-10-14: green.")
        self.assertNotIn("evidence", rows["NEW1"])
        self.assertEqual(rows["NEW1"]["depends_on"], ["CARRY1"])
        self.assertEqual(rows["NEW1"]["depends_on"], rows["NEW1"]["after"])

    def test_a_carried_unit_with_a_landed_sub_unit_is_not_owed_its_section_read(self):
        subs = [{"id": "CARRY1.a", "title": "first"}, {"id": "CARRY1.b", "title": "second", "files": ["scripts/test_carry1b.py"]}]
        seed = self.carry(closing_edit=lambda s: s["units"][0].update(sub_units=subs, owns=s["units"][0]["owns"] + ["scripts/test_carry1b.py"]),
                          edit=lambda s: s["units"][0].update(evidence="CARRY1.a landed 2026-10-14: green."))
        r = PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        lines = PG.lint_before_write(r, self.root)
        self.assertFalse([l for l in lines if "sub unit sections" in l], lines)

    def test_a_relative_root_is_resolved_before_the_lint_overlay(self):
        self.carry(extra=[unit("NEW1")])
        here = os.getcwd()
        os.chdir(self.root)
        try:
            code, out = self.run_gen(extra=["--dry"], root=".", version=NEXT)
        finally:
            os.chdir(here)
        self.assertEqual(code, 0, out)
        self.assertNotIn("is not on disk", out)

    def test_a_non_carried_row_with_a_tracked_id_still_refuses(self):
        self.carry()
        for flag in (False, None, "true", 1):  # only the boolean True carries
            self.carry_flag(flag)
            before = listing(self.root)
            code, out = self.gen_next()
            self.assertEqual(code, 1, out)
            self.assertIn("already tracked under docs/plan: CARRY1", out)
            self.assertEqual(listing(self.root), before)

    def carry_flag(self, flag):
        with open(self.seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        seed["units"][0]["carried"] = flag
        with open(self.seed_path, "w", encoding="utf-8") as f:
            json.dump(seed, f)

    def test_a_carried_id_missing_from_the_named_plan_still_refuses(self):
        self.carry(edit=lambda s: s["units"][0].update(from_plan="docs/plan/BROTHER-9.9.9-WBS.json"))  # that plan holds OLD1 only
        before = listing(self.root)
        code, out = self.gen_next()
        self.assertEqual(code, 1, out)
        self.assertIn("already tracked under docs/plan: CARRY1", out)
        self.assertEqual(listing(self.root), before)

    def test_an_unreadable_or_unsafe_from_plan_grants_no_exemption(self):
        self.put("docs/plan/not-a-plan.json", "{not json")
        self.put("docs/plan/a-list.json", "[1, 2]")
        self.put("docs/plan/no-units.json", json.dumps({"units": "x"}))
        outside = os.path.join(self.tmp, "outside-plan.json")  # a readable plan holding the id, but not inside the root
        with open(outside, "w", encoding="utf-8") as f:
            json.dump({"units": [{"id": "CARRY1"}]}, f)
        self.carry()
        for bad in ("docs/plan/not-a-plan.json", "docs/plan/a-list.json", "docs/plan/no-units.json", "docs/plan/gone.json",
                    plan_seed_no_data(), "", None, 7, ["docs/plan/BROTHER-%s-WBS.json" % VERSION], outside,
                    "../outside-plan.json", "docs/plan/../../outside-plan.json"):
            with open(self.seed_path, encoding="utf-8") as f:
                seed = json.load(f)
            seed["units"][0]["from_plan"] = bad
            with open(self.seed_path, "w", encoding="utf-8") as f:
                json.dump(seed, f)
            before = listing(self.root)
            code, out = self.gen_next()
            self.assertEqual(code, 1, "%r: %s" % (bad, out))
            self.assertIn("already tracked under docs/plan: CARRY1", out, repr(bad))
            self.assertEqual(listing(self.root), before)

    def test_collisions_exempts_a_carried_row_only_with_a_root_and_a_plan_that_holds_it(self):
        self.close_plan()
        row = {"id": "CARRY1", "carried": True, "from_plan": CLOSING}
        taken = {"CARRY1"}
        self.assertEqual(PG.collisions([row], taken, self.root), [])
        self.assertEqual(PG.collisions([row], taken), ["CARRY1"])  # no root: nothing can be read, so no exemption
        self.assertEqual(PG.collisions([dict(row, carried=False)], taken, self.root), ["CARRY1"])
        self.assertEqual(PG.collisions([dict(row, id="TAKEN1")], {"TAKEN1"}, self.root), ["TAKEN1"])  # not in that plan
        self.assertEqual(PG.collisions([row, {"id": "A"}, {"id": "CARRY1"}], {"CARRY1", "A"}, self.root), ["A", "CARRY1"])

    def test_a_carried_row_whose_spec_is_gone_or_outside_the_root_is_refused_by_name(self):
        seed = self.carry()
        os.remove(os.path.join(self.root, "docs/plan/specs/CARRY1.md"))
        with self.assertRaisesRegex(ValueError, "CARRY1.*not an existing file"):
            PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        with open(os.path.join(self.tmp, "outside.md"), "w", encoding="utf-8") as f:
            f.write("# a real file that is not inside the root\n")
        for bad in ("../outside.md", os.path.join(self.tmp, "outside.md")):
            seed["units"][0]["spec"] = bad
            with self.assertRaisesRegex(ValueError, "not an existing file inside the root"):
                PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))

    def test_a_malformed_carried_row_is_refused_by_name_and_a_plain_row_with_ids_does_not_crash(self):
        for edit, why in ((lambda s: s["units"][0].update(sub_units=[]), "sub_units"),
                          (lambda s: s["units"][0].update(sub_units=["OTHER.a"]), "sub_units"),
                          (lambda s: s["units"][0].update(sub_units="CARRY1.a"), "sub_units"),
                          (lambda s: s["units"][0].update(done_check=""), "done_check is missing"),
                          (lambda s: s["units"][0].update(worker=None), "worker is missing"),
                          (lambda s: s["units"][0].update(spec=7), "spec is missing")):
            seed = self.carry(edit=edit)
            with self.assertRaisesRegex(ValueError, why):
                PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
            self.assertEqual(self.gen_next()[0], 1)
        seed = self.carry(edit=lambda s: s["units"][0].update(carried=False))  # ids as sub units on a row that is not carried
        with self.assertRaisesRegex(ValueError, "sub_units are not rows"):
            PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))

    def test_a_dependency_on_a_carried_unit_names_its_last_sub_unit(self):
        subs = [{"id": "CARRY1.a", "title": "first"}, {"id": "CARRY1.b", "title": "second", "files": ["scripts/test_carry1.py"]}]
        seed = self.carry(extra=[unit("NEW1", after=["CARRY1"])], closing_edit=lambda s: s["units"][0].update(sub_units=subs))
        r = PG.render_plan(seed, NEXT, "2026-10-14", root=self.root, template=(HEADS, FILLS))
        manifest = json.loads(r["pack"]["docs/plan/specs/%s/pack/manifest.json" % NEXT])
        self.assertEqual({s["id"]: s["deps"] for s in manifest["subunits"]}, {"NEW1.a": ["CARRY1.b"]})


def plan_seed_no_data():
    return PS.NO_DATA


class TestSpeed(Fix, unittest.TestCase):
    def test_a_sixteen_unit_seed_renders_in_under_ten_seconds(self):
        self.make_seed([unit("U%02d" % i, reads=["scripts/code.py f (line %d)" % (i + 1)]) for i in range(16)])
        start = time.monotonic()
        code, out = self.run_gen()
        measured = time.monotonic() - start  # measured by this test, never a sleep
        self.assertEqual(code, 0, out)
        self.assertLess(measured, 10.0)
        line = [l for l in out.splitlines() if l.startswith("PLAN: ")][0]
        self.assertIn("units 16 sub units 16 batches 16", line)
        printed = float(line.split("seconds ")[1].split()[0])
        self.assertLessEqual(printed, measured + 0.05)  # the command prints a real reading, not a constant
        self.assertGreater(printed, 0.0)


if __name__ == "__main__":
    unittest.main()
