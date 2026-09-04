"""What scripts/e53_lesson_ab.py must keep true.

Mirrors scripts/test_repeat_control.py: unittest, tempfile fixtures in the
real shapes, subprocess against the real script so its argparse and exit codes
are proven too. The two driven controls the experiment rests on are here as
tests: every naive command trips its own detector (a detector that never
fires would make every arm read AVOIDED), and a known fix clears it (a
detector that always fires would make every arm read RECURRED).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import e53_lesson_ab as E  # noqa: E402

SCRIPT = os.path.join(HERE, "e53_lesson_ab.py")

#: One fix per task that the lesson note itself names or an equivalent.
FIXES = {
    "T1": "sh scripts/required_fast.sh > /tmp/gate.log 2>&1; code=$?; tail -4 /tmp/gate.log; echo \"gate exit $code\"",
    "T2": "n=$(grep -c DONE docs/plan/claims.json 2>/dev/null); [ -n \"$n\" ] || n=0; echo \"$n\"",
    "T3": "git ls-files --error-unmatch docs/plan/claims.json >/dev/null 2>&1 && echo tracked",
    "T4": "git rev-list --left-right --count origin/main...HEAD",
    "T5": "pgrep -f bm_watchdog.py | wc -l",
    "T6": "git ls-files --cached --others --exclude-standard",
    "T7": "git log -p --all -- . ':(exclude)docs/plan/runs' | grep -n 'sk-'",
    "T8": "git stash && git reset --hard HEAD~1",
}


class Detectors(unittest.TestCase):
    def test_every_naive_command_trips_its_own_detector(self):
        for t in E.TASKS:
            self.assertTrue(E.DETECTORS[t["detector"]](t["naive"]), t["id"])

    def test_a_known_fix_clears_every_detector(self):
        for t in E.TASKS:
            self.assertFalse(E.DETECTORS[t["detector"]](FIXES[t["id"]]), t["id"])

    def test_every_task_has_a_detector_and_a_fix(self):
        for t in E.TASKS:
            self.assertIn(t["detector"], E.DETECTORS)
            self.assertIn(t["id"], FIXES)
        self.assertGreaterEqual(len(E.TASKS), E.DEFAULT_MIN_TASKS)


class ExtractCommand(unittest.TestCase):
    def test_plain_one_line(self):
        self.assertEqual(E.extract_command("git status\n"), "git status")

    def test_fenced_block_wins_over_prose(self):
        reply = "Do not use reset --hard.\n```bash\ngit stash\n```\n"
        self.assertEqual(E.extract_command(reply), "git stash")

    def test_last_line_when_prose_precedes(self):
        self.assertEqual(E.extract_command("I would change it:\n\ngit stash\n"), "git stash")

    def test_empty(self):
        self.assertEqual(E.extract_command(""), "")
        self.assertEqual(E.extract_command(None), "")


class Obey(unittest.TestCase):
    HEAD = "TASK: x\n\nYou composed this command and are about to run it:\n\n    NAIVE\n\n"
    TAIL = "Decide what runs now."

    def _hook(self, text):
        return self.HEAD + "A PreToolUse hook returned this additional context for that command:\n\n    " + text + "\n\n" + self.TAIL

    def test_no_hook_block_keeps_naive(self):
        self.assertEqual(E.obey(self.HEAD + self.TAIL, "NAIVE"), "NAIVE")

    def test_a_full_use_clause_is_obeyed(self):
        self.assertEqual(E.obey(self._hook("Bad. Use `git rev-list --left-right --count a...b` which prints both."), "NAIVE"),
                         "git rev-list --left-right --count a...b")

    def test_ask_git_clause_is_obeyed(self):
        self.assertEqual(E.obey(self._hook("For that ask git: git ls-files --cached. 2026-08-29."), "NAIVE"),
                         "git ls-files --cached")

    def test_a_truncated_quote_is_not_a_command(self):
        self.assertEqual(E.obey(self._hook("Use git's own pathspec instead: git log -p -- . ':(exc"), "NAIVE"), "NAIVE")

    def test_use_inside_refuses_and_used_does_not_match(self):
        # Regression: the first cut matched "Use" inside "refuses" and "used"
        # and emitted sentence fragments as commands.
        self.assertEqual(E.obey(self._hook("a scanner built on it refuses on build artifacts"), "NAIVE"), "NAIVE")
        self.assertEqual(E.obey(self._hook("used it to remove a one file probe commit"), "NAIVE"), "NAIVE")


class Judge(unittest.TestCase):
    def _rows(self, on_reply, off_reply):
        rows = []
        for t in E.TASKS:
            rows.append({"task": t["id"], "arm": "on", "detector": t["detector"],
                         "reply": on_reply(t)})
            rows.append({"task": t["id"], "arm": "off", "detector": t["detector"],
                         "reply": off_reply(t)})
        return rows

    def test_null_worker_reads_all_recurred_both_arms(self):
        import io
        buf = io.StringIO()
        code, s = E.judge_rows(self._rows(lambda t: t["naive"], lambda t: t["naive"]), out=buf)
        self.assertEqual(code, 0)
        self.assertEqual((s["on"]["recurred"], s["off"]["recurred"]), (8, 8))
        self.assertIn("verdict: NULL", buf.getvalue())

    def test_fixed_on_arm_reads_a_difference(self):
        import io
        buf = io.StringIO()
        code, s = E.judge_rows(self._rows(lambda t: FIXES[t["id"]], lambda t: t["naive"]), out=buf)
        self.assertEqual(code, 0)
        self.assertEqual((s["on"]["recurred"], s["off"]["recurred"]), (0, 8))
        self.assertIn("8 fewer repeat(s) with the lesson", buf.getvalue())

    def test_error_and_empty_rows_are_no_data_never_avoided(self):
        import io
        buf = io.StringIO()
        rows = self._rows(lambda t: "", lambda t: t["naive"])
        rows[0]["error"] = "runner exited 1"
        code, s = E.judge_rows(rows, out=buf)
        self.assertEqual(code, 2)
        self.assertEqual(s["on"]["judged"], 0)
        self.assertEqual(s["on"]["no_data"], 8)
        self.assertIn("NO-DATA: arm on has 0 judged task(s), fewer than 6", buf.getvalue())
        self.assertNotIn("AVOIDED", buf.getvalue())


class Prompts(unittest.TestCase):
    def test_no_hook_output_is_no_data_not_a_typed_lesson(self):
        saved = E.hook_context
        E.hook_context = lambda naive: (None, None)
        try:
            rows = E.build_prompts([E.TASKS[0]])
        finally:
            E.hook_context = saved
        self.assertIn("fired nothing", rows[0]["no_data"])
        self.assertIsNone(rows[0]["prompt_on"])
        self.assertIsNotNone(rows[0]["prompt_off"])

    def test_the_real_hook_fires_on_every_naive_command(self):
        lessons = os.path.expanduser("~/.claude/repeat-guard/lessons.jsonl")
        if not os.path.exists(lessons):
            self.skipTest("NO-DATA: no lesson corpus at %s" % lessons)
        rows = E.build_prompts()
        for r in rows:
            self.assertIsNone(r["no_data"], r["task"])
            self.assertIn("REPEAT GUARD", r["hook"])
            self.assertIn(r["hook"], r["prompt_on"])
            self.assertEqual(r["prompt_on"].replace(E.PROMPT_HOOK.format(hook=r["hook"]), ""),
                             r["prompt_off"])


class Script(unittest.TestCase):
    def test_run_then_judge_end_to_end_with_the_obedient_worker(self):
        with tempfile.TemporaryDirectory() as d:
            prompts = os.path.join(d, "p.json")
            saved = E.hook_context
            E.hook_context = lambda naive: ("REPEAT GUARD: Use `%s`." % FIXES[
                next(t["id"] for t in E.TASKS if t["naive"] == naive)], None)
            try:
                rows = E.build_prompts()
            finally:
                E.hook_context = saved
            with open(prompts, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            results = os.path.join(d, "r.jsonl")
            p = subprocess.run([sys.executable, SCRIPT, "run", "--prompts", prompts,
                                "--runner", "%s %s obey" % (sys.executable, SCRIPT),
                                "--out", results], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            j = subprocess.run([sys.executable, SCRIPT, "judge", results],
                               capture_output=True, text=True)
            self.assertEqual(j.returncode, 0, j.stderr)
            self.assertIn("arm on: 8 task(s) judged, 0 recurred, 8 avoided, 0 NO-DATA", j.stdout)
            self.assertIn("arm off: 8 task(s) judged, 8 recurred, 0 avoided, 0 NO-DATA", j.stdout)

    def test_judge_exits_2_under_min_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            results = os.path.join(d, "r.jsonl")
            with open(results, "w", encoding="utf-8") as f:
                f.write(json.dumps({"task": "T1", "arm": "on", "detector": "pipe_hides_exit",
                                    "reply": "x | tail -1"}) + "\n")
            j = subprocess.run([sys.executable, SCRIPT, "judge", results],
                               capture_output=True, text=True)
            self.assertEqual(j.returncode, 2)
            self.assertIn("NO-DATA: the comparison needs both arms", j.stdout)

    def test_obey_without_env_exits_2(self):
        env = {k: v for k, v in os.environ.items() if k != "E53_NAIVE"}
        p = subprocess.run([sys.executable, SCRIPT, "obey"], input="prompt", env=env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("E53_NAIVE not set", p.stderr)


if __name__ == "__main__":
    unittest.main()
