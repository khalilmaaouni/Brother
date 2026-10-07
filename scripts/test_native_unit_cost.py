#!/usr/bin/env python3
"""test_native_unit_cost: a sub unit's LANDED line carries its native Claude spend, joined by identity (2026-10-04).

WHY. land_batch.usd_text read only the OpenRouter ledger, so every native Claude build landed with "usd NO-DATA"
while its spend sat in the Claude call ledger untied to any sub unit. The repair: the unit runner exports its folder
name as BROTHER_UNIT_RUN, claude_ledger.start writes it as unit_run on every call that runner causes (native session,
retries, helpers), claude_ledger.finish copies it onto the terminal row, and unit_ledger.native_usd tallies each run
folder by that tag. Nothing is attributed by a time window, and an untagged historical row is never counted.

HOW. Every case drives the real modules over a throwaway Claude ledger and runs folder, ending at usd_text, the one
function the LANDED line prints, so the control is tested where it lives. Python 3.9, standard library only, no
network, nothing read from the real home folder. No em or en dashes.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import claude_ledger as CL  # noqa: E402
import unit_ledger as UL  # noqa: E402
import land_batch as LB  # noqa: E402

NOW = datetime.datetime(2026, 10, 4, 6, 0, tzinfo=datetime.timezone.utc)


class NativeUnitCost(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="native-unit-cost-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.runs = os.path.join(self.root, "unit-runs")
        os.makedirs(self.runs)
        self.claude = os.path.join(self.root, "claude-calls.jsonl")
        self.provider = os.path.join(self.root, "openrouter-ledger.jsonl")   # absent: no provider spend unless written
        self.n = 0

    def run_dir(self, name, epoch=1791100000):
        """A runner folder with its RUN-TAG, as unit_runner writes it; returns the tag its calls carry."""
        os.makedirs(os.path.join(self.runs, name), exist_ok=True)
        tag = "%s@%d" % (name, epoch)
        with open(os.path.join(self.runs, name, "RUN-TAG"), "w", encoding="utf-8") as fh:
            fh.write(tag + "\n")
        return tag

    def call(self, unit_run, cost="none", at=NOW):
        """One Claude call registered under unit_run (None: untagged); cost "none" leaves it open, else it is closed."""
        self.n += 1
        env = {"BROTHER_UNIT_RUN": unit_run} if unit_run is not None else {}
        started = CL.start(self.claude, {"call": "native-x-%d" % self.n, "kind": "native", "model": "m"}, 60, env=env, now=at)
        if cost != "none":
            CL.finish(self.claude, {"call": started["call"], "cost_usd": cost}, now=at + datetime.timedelta(seconds=30))
        return started

    def text(self, sub):
        return LB.usd_text(sub, self.runs, self.provider, claude_path=self.claude)

    def test_a_native_sub_unit_shows_its_own_spend(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, 0.40)
        self.assertEqual(self.text("HP1.e"), "usd 0.40 (claude 0.40 over 1 call(s) under its runner, provider NO-DATA)")

    def test_retries_and_helpers_under_one_runner_and_repeated_runs_all_count(self):
        a, b = self.run_dir("HP1.e-053700"), self.run_dir("HP1.e-061500")
        self.call(a, 0.40); self.call(a, 0.05)   # the session and a helper under one runner
        self.call(b, 0.30)                        # a second attempt, its own folder
        self.assertEqual(self.text("HP1.e"), "usd 0.75 (claude 0.75 over 3 call(s) under its runner, provider NO-DATA)")

    def test_unrelated_and_untagged_calls_are_never_counted(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, 0.40)
        self.call("HP1.d-052723@1791100000", 9.00)    # another sub unit's runner
        self.call("HP1.ee-053700@1791100000", 9.00)   # a longer name sharing the prefix
        self.call(None, 9.00)                    # an untagged call in the same minute: never guessed in by time
        self.assertEqual(self.text("HP1.e"), "usd 0.40 (claude 0.40 over 1 call(s) under its runner, provider NO-DATA)")

    def test_a_sibling_sub_unit_whose_id_extends_this_one_is_never_counted(self):
        # HP1.e and HP1.ee: a prefix match on the folder name would bill the sibling's runner to this sub unit
        self.call(self.run_dir("HP1.e-053700"), 0.40)
        self.call(self.run_dir("HP1.ee-053700"), 9.00)
        self.assertEqual(self.text("HP1.e"), "usd 0.40 (claude 0.40 over 1 call(s) under its runner, provider NO-DATA)")

    def test_a_reused_folder_name_never_inherits_a_pruned_runs_calls(self):
        old = "HP1.e-053700@1790000000"            # a run of an earlier day whose folder was pruned
        self.call(old, 9.00)
        self.call(self.run_dir("HP1.e-053700"), 0.40)   # the same HHMMSS today, a new claim epoch
        self.assertEqual(self.text("HP1.e"), "usd 0.40 (claude 0.40 over 1 call(s) under its runner, provider NO-DATA)")

    def test_a_folder_with_no_run_tag_attributes_nothing_and_a_foreign_tag_is_unknown(self):
        os.makedirs(os.path.join(self.runs, "HP1.e-053700"))            # a run from before the tag existed
        self.call("HP1.e-053700@1791100000", 9.00)
        self.assertEqual(self.text("HP1.e"), "usd NO-DATA")
        with open(os.path.join(self.runs, "HP1.e-053700", "RUN-TAG"), "w", encoding="utf-8") as fh:
            fh.write("HP1.d-053700@1791100000\n")                       # a tag that is not this folder's own
        self.assertTrue(self.text("HP1.e").startswith("usd NO-DATA (1 Claude call(s)"), self.text("HP1.e"))

    def test_an_earlier_untagged_run_is_named_never_silently_left_out(self):
        os.makedirs(os.path.join(self.runs, "HP1.e-040000"))           # a run from before the tag existed
        self.call(self.run_dir("HP1.e-053700"), 0.40)
        self.assertEqual(self.text("HP1.e"), "usd 0.40 (claude 0.40 over 1 call(s) under its runner, provider NO-DATA)"
                         " (1 earlier run(s) before the run tag: their Claude spend is not in this figure)")

    def test_a_call_with_no_cost_makes_the_figure_no_data_with_the_known_part(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, 0.40)
        self.call(r, "none", at=NOW - datetime.timedelta(hours=3))   # expired with no terminal row: uncosted
        out = self.text("HP1.e")
        self.assertTrue(out.startswith("usd NO-DATA (1 Claude call(s) of HP1.e have no cost; 0.40 known"), out)

    def test_a_terminal_row_with_an_unusable_cost_is_unknown_never_zero(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, None)
        self.assertTrue(self.text("HP1.e").startswith("usd NO-DATA (1 Claude call(s)"), self.text("HP1.e"))

    def test_mixed_native_and_provider_spend_adds_disjoint_ledgers(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, 0.40)
        orig = UL.blended_usd_detail
        UL.blended_usd_detail = lambda sub, runs, lp: (0.13, 0)   # the provider half, already tested in h6c
        self.addCleanup(setattr, UL, "blended_usd_detail", orig)
        self.assertEqual(self.text("HP1.e"), "usd 0.53 (claude 0.40 over 1 call(s) under its runner, provider 0.13)")

    def test_a_provider_only_build_reads_as_before(self):
        self.run_dir("HP1.e-053700")
        orig = UL.blended_usd_detail
        UL.blended_usd_detail = lambda sub, runs, lp: (0.13, 0)
        self.addCleanup(setattr, UL, "blended_usd_detail", orig)
        self.assertEqual(self.text("HP1.e"), "usd 0.13")

    def test_a_duplicate_row_anywhere_in_the_ledger_makes_the_figure_unknown(self):
        r = self.run_dir("HP1.e-053700")
        self.call(r, 0.40)
        with open(self.claude, "a", encoding="utf-8") as fh:   # an orphan terminal row for another call
            fh.write(json.dumps({"at": "2026-10-04T06:00:00+00:00", "call": "ghost", "event": "done", "cost_usd": 1}) + "\n")
        self.assertTrue(self.text("HP1.e").startswith("usd NO-DATA (1 Claude call(s)"), self.text("HP1.e"))

    def test_no_call_at_all_is_no_data_never_zero(self):
        self.run_dir("HP1.e-053700")
        self.assertEqual(self.text("HP1.e"), "usd NO-DATA")

    def test_an_unreadable_claude_ledger_is_no_data(self):
        self.run_dir("HP1.e-053700")
        with open(self.claude, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
        self.assertTrue(self.text("HP1.e").startswith("usd NO-DATA"), self.text("HP1.e"))

    def test_start_tags_only_a_run_folder_name_and_finish_copies_it(self):
        for value, tagged in (("HP1.e-053700@1791100000", True), ("HP1.e-053700", False), ("HP1.e", False),
                              ("../x-053700@1791100000", False), ("", False), ("HP1.e-053700@1791100000\nx", False)):
            with self.subTest(value=value):
                row = CL.start(self.claude, {"call": "t-%d" % self.n, "kind": "native"}, 60, env={"BROTHER_UNIT_RUN": value}, now=NOW)
                self.n += 1
                self.assertEqual(row.get("unit_run") == value, tagged, row)
                done = CL.finish(self.claude, {"call": row["call"], "cost_usd": 0.01}, now=NOW)
                self.assertEqual(done.get("unit_run") == value, tagged, done)

    def test_the_unit_runner_exports_its_folder_right_after_claiming_it(self):
        # unit_runner does its work at import; its source is read here, a stated limit: the tagging itself is tested
        # through start() above, and this pins that the runner sets the variable to its own folder name
        src = open(os.path.join(HERE, "loop", "unit_runner.py"), encoding="utf-8").read()
        claim = src.index("run = claim_run_dir(")
        tag = src.index('_unit_run_tag = "%s@%d" % (os.path.basename(run), int(time.time()))')
        export = src.index('os.environ["BROTHER_UNIT_RUN"] = _unit_run_tag')
        self.assertLess(claim, tag); self.assertLess(tag, export)


if __name__ == "__main__":
    unittest.main()
