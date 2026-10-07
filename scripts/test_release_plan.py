"""The 1.1.0 release plan covers every piece of open work, in stage order, and its page is generated from it.

Owner, 2026-09-26: the hardening Gantt "is very unclear and leaves out too many of 1.1.0 needed work", and "I cannot
follow at all your work against the gantt chart". docs/plan/BROTHER-1.1.0-RELEASE-PLAN.json is the roll-up: seven
stages in dependency order, workstreams that REFERENCE units of the launch board and the hardening plan by id (their
states are read from those files, never copied), the Codex and Antigravity parity grid, and the cut requirements.
scripts/gen_release_plan.py renders docs/plan/BROTHER-LOOP-HARDENING-GANTT.html from it. This suite pins:
  - every launch board unit that is not DONE, and every hardening unit, sits in exactly one work package;
  - every audit issue of H9 appears on the page with its plan state; every parity cell exists for both hosts;
  - the page on disk is exactly what the generator renders now (no hand edit, no drift);
  - a plan that drops an open unit is named by the coverage check, never silently accepted;
  - the stage chart writes no calendar date.
Skips where the launch board is not shipped (the public export). Run: python3 -B scripts/test_release_plan.py
"""
import copy, json, os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCH = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")


@unittest.skipUnless(os.path.isfile(LAUNCH), "the launch board is not shipped in this tree")
class ReleasePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import gen_release_plan as G
        cls.G = G
        cls.plan, cls.launch, cls.hard = G.load(ROOT)

    def test_seven_stages_in_order(self):
        self.assertEqual([s["id"] for s in self.plan["stages"]], ["S%d" % i for i in range(1, 8)])

    def test_every_open_unit_is_in_exactly_one_package(self):
        self.assertEqual(self.G.coverage_problems(self.plan, self.launch, self.hard), [])

    def test_a_plan_that_drops_an_open_unit_is_named(self):
        plan = copy.deepcopy(self.plan)
        dropped = None
        for ws in plan["workstreams"]:
            for p in ws["packages"]:
                for ref in list(p.get("units") or []):
                    # an OPEN unit: dropping a DONE unit is rightly no problem (H2, the first ref, closed long ago)
                    open_ids = {u["id"] for u in self.launch["units"] if u.get("state") != "DONE"}
                    if ref.startswith("launch:") and ref.split(":")[1] in open_ids:
                        dropped = ref; p["units"].remove(ref); break
                if dropped: break
            if dropped: break
        probs = self.G.coverage_problems(plan, self.launch, self.hard)
        self.assertTrue(any(dropped.split(":")[1] in x for x in probs), probs)

    def test_every_audit_issue_is_on_the_page(self):
        page = self.G.render(self.plan, self.launch, self.hard, ROOT)
        h9 = next(u for u in self.hard["units"] if u["id"] == "H9")
        for it in h9["issues"]:
            row = re.search(r'<tr data-issue="%s">(.*?)</tr>' % re.escape(it["id"]), page, re.S)
            self.assertTrue(row, it["id"])
            self.assertIn(">%s<" % it["state"], row.group(1), it["id"])

    def test_an_experimental_host_is_shown_never_counted(self):
        # owner scope decision 2026-10-03: Antigravity is experimental in 1.1.0; its cells read NO-DATA whatever is recorded,
        # and only the other hosts' cells gate the release
        exp = set(self.plan["host_parity"].get("experimental_hosts") or [])
        self.assertEqual(exp, {"Antigravity"})
        every = self.G.parity_cells(self.plan)
        self.assertTrue(all(c["state"] == "NO-DATA" for h, d, c in every if h in exp))
        self.assertEqual({h for h, d, c in self.G.required_cells(self.plan)}, {"Codex"})
        planted = json.loads(json.dumps(self.plan))
        planted["host_parity"].setdefault("cells", {})["Antigravity|%s" % planted["host_parity"]["dimensions"][0]] = {"state": "PASS"}
        self.assertEqual({c["state"] for h, d, c in self.G.parity_cells(planted) if h == "Antigravity"}, {"NO-DATA"})

    def test_the_parity_grid_has_every_cell_for_both_hosts(self):
        hp = self.plan["host_parity"]
        self.assertEqual(set(hp["hosts"]), {"Codex", "Antigravity"})
        self.assertGreaterEqual(len(hp["dimensions"]), 10)
        cells = self.G.parity_cells(self.plan)
        self.assertEqual(len(cells), len(hp["hosts"]) * len(hp["dimensions"]))

    def test_the_page_is_what_the_generator_renders(self):
        page = open(os.path.join(ROOT, "docs", "plan", "BROTHER-LOOP-HARDENING-GANTT.html"), encoding="utf-8").read()
        self.assertEqual(page, self.G.render(self.plan, self.launch, self.hard, ROOT), "the page drifted from the plan: regenerate it")

    def test_the_stage_chart_writes_no_calendar_date(self):
        page = self.G.render(self.plan, self.launch, self.hard, ROOT)
        chart = page[page.index('id="stages"'):page.index("</section>", page.index('id="stages"'))]
        self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d|\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d", chart))

    def test_every_run_readiness_issue_is_on_the_page_with_its_lane(self):
        page = self.G.render(self.plan, self.launch, self.hard, ROOT)
        section = page[page.index('id="rr"'):page.index("</section>", page.index('id="rr"'))]
        rr = [u for u in self.hard["units"] if u["id"] == "RR"][0]
        self.assertTrue(rr["issues"])
        for i in rr["issues"]:
            self.assertIn('data-rr="%s"' % i["id"], section)
            self.assertIn(self.G.E(i["fix"]), section)


    def test_the_six_deferred_units_run_in_the_proof_not_before_the_freeze(self):
        """owner 2026-09-26 ("Follow your recommendation"): only D2 and M4 stay before the freeze; M1 M2 M3 R1 R2 R4 are
        built by the loop during RB and RC, so the scope those runs are launched with must admit them (astra audit)."""
        pk = {p["id"]: p for ws in self.plan["workstreams"] for p in ws["packages"]}
        self.assertEqual(set(pk["P1.4"]["units"]), {"launch:D2", "launch:M4"})
        six = {"launch:M1", "launch:M2", "launch:M3", "launch:R1", "launch:R2", "launch:R4"}
        proof = [p for p in pk.values() if six <= set(p.get("units") or [])]
        self.assertEqual(len(proof), 1)
        self.assertEqual(proof[0]["stages"], ["S3"])
        rx = re.compile(self.plan["loop_scope_recorded"]["regex"])
        for u in ("M1", "M2", "M3", "R1", "R2", "R4"):
            self.assertTrue(rx.search(u), u)
        # owner 2026-10-04 ("A: Widen to held 1.1.0 units"): D2 is admitted again; 2026-10-05 its supply emptied (D2.7 hand-routed,
        # plan_store.SUPPLY) and D2 stayed in scope, every sub unit held; M4 stays out
        self.assertTrue(rx.search("D2"), "D2")
        self.assertFalse(rx.search("M4"), "M4")

    def test_the_recorded_scope_is_the_proof_acceptors_one_definition(self):
        """One definition (owner 2026-10-04): the plan's loop_scope_recorded.regex is a copy of proof_accept.SCOPE, the
        string RB and RC are accepted against. A copy that drifts would launch a pair the acceptor refuses."""
        sys.path.insert(0, os.path.join(ROOT, "scripts", "loop"))
        import proof_accept
        self.assertEqual(self.plan["loop_scope_recorded"]["regex"], proof_accept.SCOPE)
        rx = re.compile(self.plan["loop_scope_recorded"]["regex"])
        for u in ("D2", "FX-31", "PR1", "OP1", "MG1"):
            self.assertTrue(rx.search(u), u)
        for u in ("SO", "FB1", "U8", "L3b", "D20", "PR1.c", "MG10", "MG1.e"):
            self.assertFalse(rx.search(u), u)

    def test_no_stage_waits_on_an_act_of_a_later_stage(self):
        """astra audit 2026-09-26: S6 required the tag and Release that S7 performs, and S4 required U8, which waits for
        the release. Every cut row says when it can close; release time rows close in S7, never in S6."""
        for c in self.plan["cut_requirements"]:
            self.assertIn(c.get("when"), ("candidate", "release"), c["req"])
        rel = [c["req"] for c in self.plan["cut_requirements"] if c["when"] == "release"]
        self.assertTrue(any("U8" in r for r in rel), rel)
        self.assertTrue(any(r.startswith("Tag") for r in rel), rel)
        ex = {s["id"]: s["exit"] for s in self.plan["stages"]}
        self.assertIn("candidate time", ex["S6"])
        self.assertIn("release time", ex["S7"])
        self.assertIn("U8", ex["S4"])


    def test_the_owners_four_decisions_of_the_day_are_recorded(self):
        """owner 2026-09-26 11:53 JST: "approve all 4". F3 accepted as a limit, F33 loop out of the public edition, RA retired,
        HOLD lifted for RB and RC once the frozen candidate and its proof rules are reported."""
        text = json.dumps(self.plan["owner_decisions"])
        for needle in ("F3", "F33", "RA", "HOLD", "approve all 4"):
            self.assertIn(needle, text)

    def test_a_retired_unit_is_neither_open_nor_done(self):
        ra = next(u for u in self.hard["units"] if u["id"] == "RA")
        self.assertEqual(ra["state"], "RETIRED")
        page = self.G.render(self.plan, self.launch, self.hard, ROOT)
        self.assertIn("RETIRED", page)

    def test_the_f33_ruling_a_is_recorded_over_the_earlier_exclusion(self):
        """owner 2026-09-26 12:3x JST: "F33 A". Keep shipping the loop tools in the public export; the 1.1.0 notes say the
        unattended loop is unsupported in the public edition. It supersedes the 11:53 exclusion, which stays on record."""
        text = json.dumps(self.plan["owner_decisions"])
        self.assertIn("F33 A", text)
        self.assertIn("unsupported in the public edition", text)
        self.assertIn("SUPERSEDED", text)

    def test_the_research_units_wait_for_s4_and_stay_outside_the_proof_scope(self):
        """owner authorized research units RL1 to RL5 (renamed from RL1, RL2, L7, L8, L9, which collided with existing
        launch specs): specified only, executed in S4 after the proof, never admitted by the scope RB and RC are launched
        with, and host parity only as NO-DATA behavioural rows."""
        ids = ("RL1", "RL2", "RL3", "RL4", "RL5")
        pk = {p["id"]: p for ws in self.plan["workstreams"] for p in ws["packages"]}
        self.assertEqual(pk["P4.8"]["stages"], ["S4"])
        lu = {u["id"]: u for u in self.launch["units"]}
        rx = re.compile(self.plan["loop_scope_recorded"]["regex"])
        for uid in ids:
            self.assertIn("launch:" + uid, pk["P4.8"]["units"])
            # specified in S4, and since 2026-10-02 RL3 to RL5 closed DONE on their own done checks; never in progress
            self.assertIn(lu[uid]["state"], ("SPECIFIED", "DONE"), uid)
            self.assertFalse(rx.search(uid), uid)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "plan", "specs", uid + ".md")), uid)
        for old in ("L7", "L8", "L9"):
            self.assertNotIn("launch:" + old, pk["P4.8"]["units"], "the colliding research ids must not return")
        cells = {(h, d): c for h, d, c in self.G.parity_cells(self.plan)}
        rows = [k for k in cells if any(re.search(r"\b%s\b" % uid, k[1]) for uid in ids)]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(cells[k]["state"] == "NO-DATA" for k in rows))


class H9Closure(unittest.TestCase):
    """astra audit 2026-09-26: H9's closing check read the chart's layout, not the state of the audit issues. H9 closes
    only when every issue is PASS and its own check, re-run now, exits 0; a PASS with no check is a claim."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import gen_release_plan as G
        cls.G = G

    def closure(self, issues, exits):
        hard = {"units": [{"id": "H9", "issues": issues}]}
        return self.G.h9_closure(hard, ROOT, run=lambda cmd: exits[cmd])

    def test_a_pass_with_no_check_is_a_claim(self):
        self.assertFalse(self.closure([{"id": "F1", "state": "PASS", "check": "none yet"}], {})[0])

    def test_a_pass_whose_check_fails_now_is_not_closed(self):
        self.assertFalse(self.closure([{"id": "F1", "state": "PASS", "check": "c1"}], {"c1": 1})[0])

    def test_an_open_issue_keeps_h9_open(self):
        self.assertFalse(self.closure([{"id": "F1", "state": "PASS", "check": "c1"}, {"id": "F2", "state": "OPEN", "check": "c2"}], {"c1": 0, "c2": 0})[0])

    def test_no_issues_is_no_data_never_closed(self):
        self.assertFalse(self.closure([], {})[0])

    def test_every_pass_rerun_green_closes(self):
        self.assertTrue(self.closure([{"id": "F1", "state": "PASS", "check": "c1"}, {"id": "F2", "state": "PASS", "check": "c2"}], {"c1": 0, "c2": 0})[0])

    @unittest.skipUnless(os.path.isfile(LAUNCH), "the plan files are not shipped in this tree")
    def test_h9_closes_on_the_closure_check(self):
        hard = json.load(open(os.path.join(ROOT, "docs", "plan", "BROTHER-LOOP-HARDENING-WBS.json")))
        h9 = next(u for u in hard["units"] if u["id"] == "H9")
        self.assertEqual(h9["done_check"], "python3 -B scripts/gen_release_plan.py --h9-closure")


@unittest.skipUnless(os.path.isfile(LAUNCH), "the launch board is not shipped in this tree")
class TaskState(unittest.TestCase):
    """Independent review 2026-09-26 (repro_plan_completion.py): the first rule read the unit's prose, so "U1.1 not
    landed", "U1.1 DONE was reverted" and "U1.10 landed" all marked U1.1 PASS. A task is PASS only when its unit is DONE
    on the board or an exact "<task> land:" commit exists that no revert names; prose alone is NO-DATA, never PASS."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import gen_release_plan as G
        cls.G = G

    def state(self, evidence, landings, unit_state="PARTIAL"):
        unit = {"id": "U1", "state": unit_state, "sub_units": ["U1.1"], "evidence": evidence}
        return self.G.leaves(ROOT, unit, [], None, landings=landings)[0][1]

    def test_negated_prose_is_not_completion(self):
        self.assertNotEqual(self.state("U1.1 not landed; waiting on review", {}), "PASS")

    def test_reverted_prose_is_not_completion(self):
        self.assertNotEqual(self.state("U1.1 DONE was reverted", {}), "PASS")

    def test_a_longer_id_is_not_this_task(self):
        self.assertNotEqual(self.state("U1.10 landed; U1.1 still open", {"U1.10": {"commit": "a" * 9, "reverted_by": None}}), "PASS")

    def test_an_exact_landing_is_completion(self):
        self.assertEqual(self.state("", {"U1.1": {"commit": "b" * 9, "reverted_by": None}}), "PASS")

    def test_a_reverted_landing_is_not_completion(self):
        self.assertNotEqual(self.state("", {"U1.1": {"commit": "c" * 9, "reverted_by": "d" * 9}}), "PASS")

    def test_prose_alone_is_no_data(self):
        self.assertEqual(self.state("U1.1 landed 2026-09-20", {}), "NO-DATA")

    def test_a_done_unit_is_complete(self):
        self.assertEqual(self.state("", {}, unit_state="DONE"), "PASS")

    def test_landings_are_read_by_exact_id_and_revert_hash(self):
        import subprocess, tempfile
        d = tempfile.mkdtemp(prefix="landings-")
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), cwd=d, env=env, capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-q", "-b", "main")
        g("commit", "-q", "--allow-empty", "-m", "U1.1 land: unit runner builds")
        g("commit", "-q", "--allow-empty", "-m", "U1.10 land: unit runner builds")
        ten = g("rev-parse", "HEAD")
        g("commit", "-q", "--allow-empty", "-m", 'Revert "U1.10 land: unit runner builds"', "-m", "This reverts commit %s." % ten)
        got = self.G.landings(d)
        self.assertIsNone(got["U1.1"]["reverted_by"])
        self.assertIsNotNone(got["U1.10"]["reverted_by"])
        self.assertNotIn("U1", got)


class F33EditionNote(unittest.TestCase):
    """owner 2026-09-26 12:3x JST, "F33 A": the loop tools keep shipping in the public export, and the 1.1.0 note states
    that unattended brother.loop and repair.loop are unsupported in the public edition. The subject is the paragraph the
    cut actually reads (release_note_from_tree.extra_notes), never a fixture: a missing or unreadable slot is a failure."""
    def test_the_1_1_0_note_carries_the_f33_disclosure(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import release_note_from_tree as R
        text = R.extra_notes("1.1.0")
        self.assertTrue(text, "docs/releases/1.1.0.notes.txt is missing, empty or unreadable")
        # One sentence, pinned whole: separate words let "supported in the public edition; other modes are unsupported"
        # pass (M-F33-SUPPORTED-WITH-DECOY survived that form of this test). Sentence from Codex B9's own check.
        self.assertRegex(" ".join(text.split()), r"Unattended operation of brother\.loop and repair\.loop is unsupported "
                         r"in the public edition in 1\.1\.0\.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
