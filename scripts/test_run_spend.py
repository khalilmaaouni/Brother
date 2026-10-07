#!/usr/bin/env python3
"""Tests for scripts/run_spend.py and the money ceiling it reports.

DRIVEN BOTH WAYS, because a test that cannot fail verifies nothing. The
ceiling is proven present on a claude worker AND proven ABSENT on a codex
worker; the report is proven to sum real counts AND to print NO-DATA for a
field nobody reported; and the trap is driven directly, with a fixture that
holds only the inert {"tokens": 0, "minutes": 0} shape loop_bridge.py
returns for a worker that never ran, which must never be read as a run that
spent nothing.

Python 3.9 floor, standard library only, no network.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_worker  # noqa: E402
import run_spend  # noqa: E402


def write_sidecar(root, run_name, payload):
    """One run directory holding one claims_usage.json, the exact shape and
    name loop_bridge.usage_sidecar_path() produces."""
    run_dir = os.path.join(root, run_name)
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "claims" + run_spend.SIDECAR_SUFFIX)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


class TheCeilingReachesTheClaudeWorker(unittest.TestCase):
    """A dollar ceiling is only a control if it lands in the argv."""

    def test_unset_changes_nothing(self):
        self.assertEqual(model_worker._default_argv({}),
                         model_worker._claude_argv({}))

    def test_set_appends_the_flag_before_the_prompt(self):
        argv = model_worker._default_argv({model_worker.BUDGET_ENV: "5"})
        self.assertEqual(argv[-2:], ["--max-budget-usd", "5"])
        self.assertEqual(argv[:len(model_worker.CLAUDE_ARGV)],
                         list(model_worker.CLAUDE_ARGV))
        # The prompt is the final positional, added afterwards, so the flag
        # must sit ahead of it and never after.
        self.assertNotIn("--max-budget-usd", model_worker.CLAUDE_ARGV)

    def test_a_fraction_survives(self):
        argv = model_worker._default_argv({model_worker.BUDGET_ENV: "2.50"})
        self.assertEqual(argv[-1], "2.5")

    def test_the_flag_is_the_one_the_cli_documents(self):
        """Guards against a rename drifting out of step with `claude --help`,
        whose line reads: --max-budget-usd <amount>  Maximum dollar amount to
        spend on API calls (only works with --print). The -p that satisfies
        "only works with --print" must stay in the argv too."""
        argv = model_worker._default_argv({model_worker.BUDGET_ENV: "1"})
        self.assertIn("-p", argv)
        self.assertIn("--max-budget-usd", argv)


class TheCeilingDegradesHonestly(unittest.TestCase):
    """The half that matters at 3am: when the ceiling cannot be enforced,
    nothing may imply that it is."""

    def test_codex_has_no_flag_and_says_so(self):
        env = {model_worker.BUDGET_ENV: "5",
               model_worker.MODEL_CLIENT_ENV: "codex"}
        argv = model_worker._default_argv(env)
        self.assertEqual(argv, list(model_worker.CODEX_ARGV))
        self.assertNotIn("--max-budget-usd", argv)
        usd, enforced, why = model_worker.budget_ceiling(env)
        self.assertEqual(usd, 5.0)
        self.assertFalse(enforced)
        self.assertIn("NOT enforced", why)

    def test_a_custom_command_is_not_assumed_to_take_the_flag(self):
        env = {model_worker.BUDGET_ENV: "5", "MODEL_WORKER_CMD": "echo hi"}
        usd, enforced, why = model_worker.budget_ceiling(env)
        self.assertEqual(usd, 5.0)
        self.assertFalse(enforced)
        self.assertIn("NOT passed", why)

    def test_a_junk_amount_is_refused_rather_than_guessed(self):
        for raw in ("lots", "", "0", "-3"):
            usd, enforced, _why = model_worker.budget_ceiling(
                {model_worker.BUDGET_ENV: raw})
            self.assertIsNone(usd, raw)
            self.assertFalse(enforced, raw)
        # And a refused amount never reaches the argv.
        env = {model_worker.BUDGET_ENV: "lots"}
        self.assertEqual(model_worker._default_argv(env),
                         model_worker._claude_argv(env))

    def test_the_report_says_not_enforced_for_a_codex_worker(self):
        line = run_spend._ceiling_line({model_worker.BUDGET_ENV: "5",
                                        model_worker.MODEL_CLIENT_ENV: "codex"})
        self.assertIn("NOT ENFORCED", line)

    def test_the_report_says_enforced_for_a_claude_worker(self):
        line = run_spend._ceiling_line({model_worker.BUDGET_ENV: "5"})
        self.assertIn("ENFORCED", line)
        self.assertNotIn("NOT ENFORCED", line)


class TheReportSumsWhatWasReallyReported(unittest.TestCase):

    def test_two_runs_sum_and_a_missing_field_stays_nodata(self):
        with tempfile.TemporaryDirectory() as root:
            write_sidecar(root, "run-a",
                          {"u1": {"tokens_in": 10, "tokens_out": 100,
                                  "tokens_cached": 5},
                           "u2": {"tokens_in": 4, "tokens_out": 40,
                                  "tokens_cached": 1}})
            write_sidecar(root, "run-b",
                          {"u3": {"tokens_in": 6, "tokens_out": 60,
                                  "tokens_cached": 2}})
            grand, runs = run_spend.totals_under(root)
            self.assertEqual(runs, 2)
            self.assertEqual(grand["tokens_in"], 20)
            self.assertEqual(grand["tokens_out"], 200)
            self.assertEqual(grand["tokens_cached"], 8)
            # Nobody reported the cache-write count, so it must be absent
            # from the totals and print NO-DATA, never 0.
            self.assertNotIn("tokens_cache_write", grand)
            text = "\n".join(run_spend.build_report(root, {})[0])
            self.assertIn("tokens_cache_write %s" % run_spend.NODATA, text)
            self.assertNotIn("tokens_cache_write 0", text)

    def test_no_sidecar_is_nodata_and_exit_2(self):
        with tempfile.TemporaryDirectory() as root:
            lines, runs = run_spend.build_report(root, {})
            self.assertEqual(runs, 0)
            self.assertIn(run_spend.NODATA, lines[0])
            code = run_spend.main(["--root", root])
            self.assertEqual(code, run_spend.EXIT_NODATA)

    def test_a_real_run_exits_0(self):
        with tempfile.TemporaryDirectory() as root:
            write_sidecar(root, "run-a", {"u1": {"tokens_in": 1}})
            self.assertEqual(run_spend.main(["--root", root]),
                             run_spend.EXIT_OK)

    def test_a_broken_sidecar_is_named_not_counted(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run-broken")
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "claims_usage.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{not json")
            lines, runs = run_spend.build_report(root, {})
            self.assertEqual(runs, 0)
            self.assertIn("run-broken", "\n".join(lines))
            self.assertIn("not valid JSON", "\n".join(lines))


class TheTrapIsDrivenDirectly(unittest.TestCase):
    """loop_bridge.py returns {"tokens": 0, "minutes": 0} for a worker that
    never ran. A reader that treated those keys as usage would report a
    confident zero for a run that really spent money. Driven here rather
    than trusted to a comment."""

    def test_the_inert_zero_shape_is_never_read_as_spend(self):
        with tempfile.TemporaryDirectory() as root:
            write_sidecar(root, "run-inert",
                          {"u1": {"tokens": 0, "minutes": 0}})
            grand, runs = run_spend.totals_under(root)
            self.assertEqual(grand, {})
            self.assertEqual(runs, 0)
            text = "\n".join(run_spend.build_report(root, {})[0])
            self.assertIn(run_spend.NODATA, text)

    def test_the_inert_shape_beside_a_real_one_does_not_dilute_it(self):
        with tempfile.TemporaryDirectory() as root:
            write_sidecar(root, "run-mixed",
                          {"u1": {"tokens": 0, "minutes": 0},
                           "u2": {"tokens_in": 7}})
            grand, runs = run_spend.totals_under(root)
            self.assertEqual(grand, {"tokens_in": 7})
            self.assertEqual(runs, 1)


class NoDollarsAreEverInvented(unittest.TestCase):

    def test_dollars_are_nodata_with_a_reason(self):
        with tempfile.TemporaryDirectory() as root:
            write_sidecar(root, "run-a",
                          {"u1": {"tokens_in": 10, "tokens_out": 100}})
            text = "\n".join(run_spend.build_report(root, {})[0])
            self.assertIn("dollars_spent: %s" % run_spend.NODATA, text)
            self.assertIn("no adapter", text)
            payload = run_spend.build_json(root, {})
            self.assertTrue(str(payload["dollars_spent"]).startswith(
                run_spend.NODATA))

    def test_no_price_table_is_checked_in(self):
        """A price per token anywhere in this module would be the stale
        number the docstring refuses to print."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "run_spend.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        for word in ("PRICE", "price_per", "USD_PER", "per_million"):
            self.assertNotIn(word, source)


class TheFieldNamesComeFromTheProducer(unittest.TestCase):

    def test_fields_match_model_workers_own_map(self):
        """If model_worker renames a usage field, this reader must not be
        left summing a name nobody writes."""
        self.assertEqual(set(run_spend.FIELDS),
                         set(model_worker.USAGE_FIELD_MAP))


if __name__ == "__main__":
    unittest.main()
