#!/usr/bin/env python3
"""Regression tests for tools/bm_idle.py, the mechanical detector for the
2026-08-11 planning defect: a plan whose completion leaves the machine idle
is not finished being written. This suite proves the load-bearing rule
(blocked items never count toward depth, because a blocked item cannot save
a night), the four verdict-precedence gates of `check`, and that the tool
never mistakes a crash for a pass.

Every fixture is a tempfile.TemporaryDirectory(), never the real
docs/plan/QUEUE.json. Clocks are fixed epoch integers passed via --now so no
test depends on how fast the machine runs.

Standard library only. Run: python3 tools/test_bm_idle.py
"""
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_idle.py")

#: Fixed epoch instants. NOW is the reference "current" instant used by the
#: check() fixtures below; HARD_STOP sits an hour after it so tests 7 to 9
#: (which pass --now NOW) never trip the WINDOW-CLOSED gate by accident, and
#: test 10 moves --now past HARD_STOP on purpose to trip it.
NOW = 1755000000
HARD_STOP = NOW + 3600
RECENT_FILE = NOW - 60          # 1 minute before NOW: inside a 25 minute window
STALE_FILE = NOW - 3600         # 60 minutes before NOW: outside a 25 minute window


def iso(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc).isoformat()


def run_cli(*args):
    """Invoke the CLI as a real subprocess, with BROTHERMODE_ROOT scrubbed so
    a variable set on the developer's own machine can never leak into a test
    that expects an explicit --root to decide the outcome."""
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    return subprocess.run(
        [sys.executable, TOOL_PATH] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


def write_queue(directory, obj, name="QUEUE.json"):
    path = os.path.join(directory, name)
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)
    return path


def write_text(directory, relpath, content="placeholder\n"):
    path = os.path.join(directory, relpath)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def stamp(path, mtime):
    os.utime(path, (mtime, mtime))


def item(item_id, state, title=None, done_check="", files=None):
    return {
        "id": item_id,
        "title": title or ("title of %s" % item_id),
        "state": state,
        "done_check": done_check,
        "files": files or [],
    }


def build_check_queue(min_depth=1, hard_stop_epoch=HARD_STOP,
                       unattended=True, idle_window_minutes=25,
                       watch_paths=("watched",), extra_items=None,
                       with_window=True):
    """A queue healthy enough to reach the window/idle gates of check():
    two queued items, min_depth satisfied. Used by tests 7 to 10, which
    each vary exactly one axis (recent vs stale file, attended vs not,
    now vs hard stop) so a failure points at the one gate that broke."""
    items = [item("Q1", "queued"), item("Q2", "queued")]
    if extra_items:
        items.extend(extra_items)
    data = {
        "schema": 1,
        "min_depth": min_depth,
        "idle_window_minutes": idle_window_minutes,
        "watch_paths": list(watch_paths),
        "items": items,
    }
    if with_window:
        data["window"] = {
            "hard_stop": iso(hard_stop_epoch),
            "unattended": unattended,
        }
    return data


class DepthTests(unittest.TestCase):
    """Test 1: depth counts only 'queued'; blocked is excluded on purpose."""

    def test_depth_counts_only_queued_and_excludes_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [
                    item("Q1", "queued"), item("Q2", "queued"),
                    item("Q3", "queued"),
                    item("B1", "blocked"), item("B2", "blocked"),
                ],
            }
            queue = write_queue(tmp, data)
            result = run_cli("depth", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DEPTH 3", result.stdout)
        self.assertIn("blocked=2", result.stdout)


class NoDataTests(unittest.TestCase):
    """Tests 2, 3, 4: the file cannot be used, so the verdict is NEVER a
    pass, no matter which verb was asked for."""

    def test_unknown_state_value_is_no_data_and_names_the_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [item("X9", "weird")],
            }
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"))
        self.assertIn("X9", result.stdout)
        self.assertIn("weird", result.stdout)

    def test_missing_queue_file_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = os.path.join(tmp, "does-not-exist.json")
            result = run_cli("check", "--queue", bogus, "--root", tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"))

    def test_invalid_json_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = write_text(tmp, "QUEUE.json", "{ not json at all")
            result = run_cli("check", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"))


class QueueEmptyAndDepthLowTests(unittest.TestCase):
    """Tests 5 and 6: the two exit-1 verdicts reachable before any window
    logic runs. Test 5 in particular proves blocked items cannot rescue
    depth back above zero."""

    def test_zero_queued_is_queue_empty_even_with_several_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [
                    item("B1", "blocked"), item("B2", "blocked"),
                    item("B3", "blocked"), item("D1", "done"),
                ],
            }
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("QUEUE-EMPTY:"))
        self.assertIn("3", result.stdout)  # blocked count named

    def test_depth_below_min_depth_is_depth_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 5,
                "items": [item("Q1", "queued"), item("Q2", "queued"),
                          item("Q3", "queued")],
            }
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("DEPTH-LOW:"))
        self.assertIn("3", result.stdout)
        self.assertIn("5", result.stdout)


class WindowAndIdleTests(unittest.TestCase):
    """Tests 7 to 10: the window/idle gates, each isolating one axis at a
    time against the shared build_check_queue() fixture."""

    def test_ok_when_healthy_depth_and_file_touched_just_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            watched = write_text(tmp, "watched/note.txt")
            stamp(watched, RECENT_FILE)
            data = build_check_queue()
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp,
                              "--now", iso(NOW))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("OK:"),
                         result.stdout)

    def test_idle_when_healthy_depth_but_every_watched_file_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            watched = write_text(tmp, "watched/note.txt")
            stamp(watched, STALE_FILE)
            data = build_check_queue(unattended=True)
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp,
                              "--now", iso(NOW))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("IDLE:"), result.stdout)

    def test_attended_session_is_not_accused_of_idling(self):
        with tempfile.TemporaryDirectory() as tmp:
            watched = write_text(tmp, "watched/note.txt")
            stamp(watched, STALE_FILE)
            data = build_check_queue(unattended=False)
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp,
                              "--now", iso(NOW))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("OK:"), result.stdout)

    def test_window_closed_wins_over_idle_once_hard_stop_has_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            watched = write_text(tmp, "watched/note.txt")
            stamp(watched, STALE_FILE)
            data = build_check_queue(unattended=True)
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp,
                              "--now", iso(HARD_STOP + 10))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("WINDOW-CLOSED:"),
                         result.stdout)


class NextTests(unittest.TestCase):
    """Tests 11 and 12: array-order selection and the all-done failure."""

    def test_next_returns_first_queued_in_array_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [
                    item("D1", "done", title="already finished"),
                    item("Q1", "queued", title="first queued",
                         done_check="python3 tools/test_x.py"),
                    item("Q2", "queued", title="second queued"),
                ],
            }
            queue = write_queue(tmp, data)
            result = run_cli("next", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Q1", result.stdout.splitlines()[0])
        self.assertNotIn("Q2", result.stdout.splitlines()[0])
        self.assertIn("python3 tools/test_x.py", result.stdout)

    def test_next_on_all_done_queue_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [item("D1", "done"), item("D2", "done")],
            }
            queue = write_queue(tmp, data)
            result = run_cli("next", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"))
        self.assertIn("nothing queued", result.stdout)


class MissingWatchPathTests(unittest.TestCase):
    """Test 13: a watch_paths entry that does not exist on disk is skipped
    and named, never a crash."""

    def test_missing_watch_path_is_skipped_and_named_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = build_check_queue(watch_paths=("does-not-exist",))
            queue = write_queue(tmp, data)
            result = run_cli("check", "--queue", queue, "--root", tmp,
                              "--now", iso(NOW))
        self.assertIn(result.returncode, (0, 1),
                      "a missing watch path must never crash the tool: "
                      + result.stdout + result.stderr)
        self.assertIn("does-not-exist", result.stdout)


class ListTests(unittest.TestCase):
    """`list` verb: one line per item in array order, plus an empty-items
    NO-DATA case that the other verbs do not share."""

    def test_list_prints_one_line_per_item_in_array_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [item("Q1", "queued"), item("D1", "done")],
            }
            queue = write_queue(tmp, data)
            result = run_cli("list", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("queued Q1"), lines)
        self.assertTrue(lines[1].startswith("done D1"), lines)

    def test_empty_items_list_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {"schema": 1, "min_depth": 1, "items": []}
            queue = write_queue(tmp, data)
            result = run_cli("list", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("NO-DATA:"))


class PurityTests(unittest.TestCase):
    """EFFECT CLASS pure_read is a hard requirement: the tool must write
    NOTHING, ever, regardless of verdict or verb."""

    def test_tree_is_byte_for_byte_unchanged_after_a_check_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            watched = write_text(tmp, "watched/note.txt")
            stamp(watched, RECENT_FILE)
            data = build_check_queue()
            queue = write_queue(tmp, data)

            def snapshot():
                out = {}
                for dirpath, dirnames, filenames in os.walk(tmp):
                    dirnames.sort()
                    for name in sorted(filenames):
                        path = os.path.join(dirpath, name)
                        out[os.path.relpath(path, tmp)] = os.path.getmtime(path)
                return out

            before = snapshot()
            run_cli("check", "--queue", queue, "--root", tmp, "--now", iso(NOW))
            after = snapshot()
        self.assertEqual(before, after,
                         "bm_idle.py changed the tree it inspected; this "
                         "tool must be provably read-only")


class TestChainStage(unittest.TestCase):
    """The north-star chain finding (docs/NORTH-STAR-CHAIN.md, founder
    direction 2026-08-15): a queued item names the stage of the chain it
    serves. The rule this suite pins is the ASYMMETRY, because it is the part
    that would be easy to get backwards later: an absent stage is REPORTED
    and changes nothing, an unrecognised stage is a HARD ERROR. One is work
    nobody has classified yet; the other is a typo that would file work under
    a stage nobody will ever look at."""

    def _queue(self, items):
        return {"schema": 1, "min_depth": 1, "idle_window_minutes": 25,
                "watch_paths": ["watched"], "items": items}

    def test_a_missing_stage_is_reported_and_the_verdict_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = write_queue(tmp, self._queue(
                [item("Q1", "queued"), item("Q2", "queued")]))
            result = run_cli("check", "--queue", queue, "--root", tmp,
                             "--now", iso(NOW))
            lines = result.stdout.strip().splitlines()
            self.assertTrue(lines[0].startswith("OK: depth 2"), result.stdout)
            self.assertIn("CHAIN: 2 queued item(s) name no stage", result.stdout)
            self.assertEqual(0, result.returncode,
                             "an unplaced item is a planning fact, never an exit code")

    def test_a_staged_item_produces_no_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = item("Q1", "queued")
            one["stage"] = "verified-reality"
            two = item("Q2", "queued")
            two["stage"] = "human-decision"
            queue = write_queue(tmp, self._queue([one, two]))
            result = run_cli("check", "--queue", queue, "--root", tmp,
                             "--now", iso(NOW))
            self.assertNotIn("CHAIN:", result.stdout, result.stdout)

    def test_an_unrecognised_stage_is_a_hard_error_naming_the_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = item("Q1", "queued")
            bad["stage"] = "verifed-reality"          # the typo, deliberately
            queue = write_queue(tmp, self._queue([bad, item("Q2", "queued")]))
            result = run_cli("check", "--queue", queue, "--root", tmp,
                             "--now", iso(NOW))
            self.assertIn("Q1", result.stdout + result.stderr)
            self.assertIn("not on the chain", result.stdout + result.stderr)
            self.assertNotEqual(0, result.returncode)

    def test_only_queued_items_are_counted(self):
        """A done or blocked item nobody classified is not a planning defect:
        blocked work cannot be started and done work is finished."""
        with tempfile.TemporaryDirectory() as tmp:
            staged = item("Q1", "queued")
            staged["stage"] = "risk"
            queue = write_queue(tmp, self._queue(
                [staged, item("D1", "done"), item("B1", "blocked")]))
            result = run_cli("check", "--queue", queue, "--root", tmp,
                             "--now", iso(NOW))
            self.assertNotIn("CHAIN:", result.stdout, result.stdout)

    def test_release_is_not_a_stage_any_item_may_claim(self):
        """The host performs the release; both products stop at the pull
        request. An item claiming to serve it is a scope error, so the same
        hard path catches it."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = item("Q1", "queued")
            bad["stage"] = "release"
            queue = write_queue(tmp, self._queue([bad, item("Q2", "queued")]))
            result = run_cli("check", "--queue", queue, "--root", tmp,
                             "--now", iso(NOW))
            self.assertNotEqual(0, result.returncode)


class ReconcileTests(unittest.TestCase):
    """M31's own done_check, verbatim: a command re-evaluates every done
    item whose done_check names a runnable check and reports CONFIRMED,
    STILL OPEN or CANNOT VERIFY per item, refusing to report CONFIRMED for
    any item whose done_check it could not execute. This fixture holds one
    wrongly closed item (references that no longer exist), one item whose
    reference resolves, and one item whose done_check names nothing at all
    to check, mirroring the real "doctor check 9" drift M31 describes."""

    def _fixture(self, tmp):
        write_text(tmp, "tools/existing_helper.py",
                   "def test_helper_example():\n    pass\n")
        good = item("GOOD1", "done", done_check=(
            "run tools/existing_helper.py and confirm "
            "test_helper_example passes"))
        wrong = item("WRONG1", "done", done_check=(
            "run tools/does_not_exist.py and confirm "
            "TestNoSuchClass passes"))
        no_ref = item("NOREF1", "done", done_check="doctor check 9")
        data = {
            "schema": 1,
            "min_depth": 1,
            "items": [good, wrong, no_ref, item("Q1", "queued")],
        }
        return write_queue(tmp, data)

    def test_wrongly_closed_item_is_still_open_and_never_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self._fixture(tmp)
            result = run_cli("reconcile", "--queue", queue, "--root", tmp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WRONG1 STILL OPEN", result.stdout)
        self.assertIn("tools/does_not_exist.py", result.stdout)
        self.assertIn("TestNoSuchClass", result.stdout)
        # This is the assertion M31 names by name: the wrongly closed item
        # must never be reported CONFIRMED.
        self.assertNotIn("WRONG1 CONFIRMED", result.stdout)
        self.assertFalse(
            any(line.startswith("WRONG1") and "CONFIRMED" in line
                for line in result.stdout.splitlines()),
            "a wrongly closed item was reported CONFIRMED: " + result.stdout)

    def test_item_with_no_extractable_reference_is_cannot_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self._fixture(tmp)
            result = run_cli("reconcile", "--queue", queue, "--root", tmp)
        self.assertIn("NOREF1 CANNOT VERIFY", result.stdout)
        self.assertNotIn("NOREF1 CONFIRMED", result.stdout)

    def test_item_with_a_resolving_reference_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self._fixture(tmp)
            result = run_cli("reconcile", "--queue", queue, "--root", tmp)
        self.assertNotIn("GOOD1 STILL OPEN", result.stdout)
        self.assertNotIn("GOOD1 CANNOT VERIFY", result.stdout)
        self.assertIn(
            "reconcile: 3 done items, 1 confirmed, 1 still open, "
            "1 cannot verify", result.stdout)

    def test_files_entry_that_is_not_path_shaped_is_cannot_verify(self):
        # A "files" entry with no "/" and no dot-extension, like the name
        # of a sibling repository ("BrotherSBE"), is not a path in this
        # tree. It must contribute zero references, so an item whose only
        # files entry looks like that falls to CANNOT VERIFY, never to
        # CONFIRMED (absent evidence is never a pass) and never to a false
        # STILL OPEN against a path that was never real to begin with.
        with tempfile.TemporaryDirectory() as tmp:
            bare_name = item("BARE1", "done", done_check="see the sibling",
                             files=["BrotherSBE"])
            data = {
                "schema": 1,
                "min_depth": 1,
                "items": [bare_name],
            }
            queue = write_queue(tmp, data)
            result = run_cli("reconcile", "--queue", queue, "--root", tmp)
        self.assertIn("BARE1 CANNOT VERIFY", result.stdout,
                       "a non-path-shaped files entry must yield no "
                       "reference, routing the item to CANNOT VERIFY: "
                       + result.stdout)
        self.assertNotIn("BARE1 CONFIRMED", result.stdout,
                         "an item with zero extractable references must "
                         "never be reported CONFIRMED: " + result.stdout)

    def test_output_carries_the_wrote_nothing_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = self._fixture(tmp)
            result = run_cli("reconcile", "--queue", queue, "--root", tmp)
        self.assertIn(
            "This command proposes and does not dispose. It wrote nothing.",
            result.stdout)

    def test_real_queue_file_is_untouched_by_a_reconcile_run(self):
        real_queue = os.path.join(ROOT, "docs", "plan", "QUEUE.json")
        with io.open(real_queue, "rb") as handle:
            before = hashlib.sha256(handle.read()).hexdigest()
        result = run_cli("reconcile", "--root", ROOT)
        with io.open(real_queue, "rb") as handle:
            after = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, after,
                         "reconcile must never write to the real queue file")


if __name__ == "__main__":
    unittest.main()
