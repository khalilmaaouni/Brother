#!/usr/bin/env python3
"""Tests for scripts/portable_pack.py: R25.2's weekly half and R25.4.

Hermetic where the real collect would be slow (a temp git repo, a fixture
roadmap doc, a fixture board html), and one test runs the REAL collect
against this worktree, per the task's own instruction. The portability
proof is driven backwards too: corrupt one member and --verify names it.

No em or en dashes.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS, "portable_pack.py")
sys.path.insert(0, SCRIPTS)

import portable_pack as PP  # noqa: E402
import gen_readiness_board as GB  # noqa: E402
import handover_ceremony as HC  # noqa: E402


def valid_row(id_, status, gate="G1", wave=1, depends_on=None):
    """A roadmap row satisfying gen_readiness_board's own ROW_CONTRACT_FIELDS,
    the same minimal fixture shape scripts/test_gen_readiness_board.py uses,
    so validate() actually passes and render() is exercised for real."""
    return {
        "id": id_, "gate": gate, "wave": wave, "title": "row %s" % id_,
        "detail": "d", "depends_on": depends_on or [], "owner": "o",
        "status": status, "done_check": "c", "watchdog_verify": "v",
        "owns": [], "ships": "s", "role": "r", "why_now": "w", "effect": "e",
        "visible_when": "when", "persona": "P1", "their_moment": "m",
        "what_they_see": "sees",
    }


def valid_doc(rows):
    return {
        "gates": [{"id": "G1", "title": "gate one", "size": "s",
                  "status": "OPEN", "blocker": ""}],
        "rows": rows,
    }


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def git_init(cwd):
    for args in (["init", "-q"], ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                       text=True, timeout=30, check=True)
    with open(os.path.join(cwd, "f.txt"), "w", encoding="utf-8") as f:
        f.write("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=cwd, capture_output=True,
                   text=True, timeout=30, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=cwd,
                   capture_output=True, text=True, timeout=30, check=True)


def fake_collect(repos):
    """A stand-in for handover_ceremony.collect_state, fast and hermetic."""
    return {
        "repos": {r: {"head": "deadbeefcafebabe0000000000000000000000",
                      "clean": True, "dirty_count": 0, "dirty_paths": []}
                 for r in repos},
        "sbe_tasks": {"count": 0, "tasks": []},
        "day_plan": {"ready": [], "in_flight": ["W1-X"], "event_wait": []},
        "pull_requests": {"count": 0, "pull_requests": []},
    }


class RoadmapCounts(unittest.TestCase):
    def test_done_versus_open_matches_gen_readiness_board_counts(self):
        doc = valid_doc([valid_row("R1", "DONE"), valid_row("R2", "OPEN"),
                        valid_row("R3", "DONE")])
        done, open_, total = PP.roadmap_counts(doc)
        self.assertEqual((done, open_, total), (2, 1, 3))


class BoardHtmlRendering(unittest.TestCase):
    def test_explicit_path_wins_over_rendering(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "b.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write("<p>fixture board</p>")
            out = PP.render_board_html(valid_doc([valid_row("R1", "DONE")]),
                                       board_html_path=p)
            self.assertEqual(out, "<p>fixture board</p>")

    def test_valid_doc_renders_through_gen_readiness_board(self):
        doc = valid_doc([valid_row("R1", "DONE"), valid_row("R2", "OPEN")])
        html = PP.render_board_html(doc)
        self.assertIn("Brother Readiness Board", html)
        m = PP.ROWS_DONE_RE.search(html)
        self.assertIsNotNone(m)
        self.assertEqual((m.group(1), m.group(2)), ("1", "2"))

    def test_invalid_doc_falls_back_to_disk_and_names_why_when_absent(self):
        bad_doc = {"gates": [], "rows": [{"id": "R1"}]}  # fails validate()
        real_output = GB.OUTPUT
        moved = real_output + ".portable-pack-test-moved"
        had_real = os.path.isfile(real_output)
        if had_real:
            os.rename(real_output, moved)
        try:
            with self.assertRaises(ValueError):
                PP.render_board_html(bad_doc)
        finally:
            if had_real:
                os.rename(moved, real_output)


class LimitState(unittest.TestCase):
    def test_non_dict_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "l.json")
            write_json(p, [1, 2, 3])
            with self.assertRaises(ValueError):
                PP.load_limit_state(p)

    def test_valid_dict_loads(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "l.json")
            write_json(p, {"class": "seven_day", "reset_at": "2026-09-01T00:00:00Z"})
            self.assertEqual(PP.load_limit_state(p)["class"], "seven_day")


class StartHereAndMegaPrompt(unittest.TestCase):
    def test_start_here_names_limit_state_when_given(self):
        text = PP.build_start_here(fake_collect(["."]), [],
                                   {"class": "weekly", "reset_at": "T"},
                                   5, 2, 7, ["R9"])
        self.assertIn("class: weekly", text)
        self.assertIn("reset at: T", text)
        self.assertIn("DONE: 5   OPEN: 2   (of 7 total rows)", text)

    def test_start_here_names_no_limit_state_when_absent(self):
        text = PP.build_start_here(fake_collect(["."]), [], None, 1, 1, 2, [])
        self.assertIn("none recorded; this pack was not built from a "
                     "limit pause", text)

    def test_mega_prompt_has_no_local_path_outside_clone_lines(self):
        text = PP.build_mega_prompt(3, 1, 4, ["R1"])
        for line in text.splitlines():
            if line.strip().startswith("git clone"):
                continue
            self.assertIsNone(PP.PATH_PATTERN.search(line),
                             "found a local path outside a clone line: %r" % line)

    def test_mega_prompt_names_the_three_public_repos(self):
        text = PP.build_mega_prompt(0, 0, 0, [])
        self.assertIn("git clone https://github.com/khalilmaaouni/Brother.git", text)
        self.assertIn("git clone https://github.com/khalilmaaouni/BrotherModeUp.git", text)
        self.assertIn("git clone https://github.com/khalilmaaouni/BrotherSBE.git", text)

    def test_mega_prompt_bakes_in_board_position_as_text(self):
        text = PP.build_mega_prompt(9, 3, 12, ["R5", "R6"])
        self.assertIn("Of 12 roadmap rows: 9 DONE, 3 OPEN. Ready now: R5, R6.", text)


class BuildAndVerifyHermetic(unittest.TestCase):
    """The main round trip, with a fake collect and a fixture roadmap and
    board, so it runs fast and never touches this worktree's real state."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.roadmap_path = os.path.join(self.td.name, "roadmap.json")
        write_json(self.roadmap_path, valid_doc(
            [valid_row("R1", "DONE"), valid_row("R2", "DONE"),
             valid_row("R3", "OPEN")]))
        self.board_path = os.path.join(self.td.name, "board.html")
        with open(self.board_path, "w", encoding="utf-8") as f:
            f.write('<span class="v">2/3</span><span class="l">rows done</span>')
        self.out_dir = os.path.join(self.td.name, "out")

    def _build(self, **kw):
        kw.setdefault("collect", fake_collect)
        kw.setdefault("roadmap_path", self.roadmap_path)
        kw.setdefault("board_html_path", self.board_path)
        kw.setdefault("out_dir", self.out_dir)
        kw.setdefault("today", "2026-08-30")
        return PP.build_pack(["."], **kw)

    def test_one_zip_law_exactly_one_zip_in_out_dir(self):
        zip_path, had_no_data = self._build()
        self.assertFalse(had_no_data)
        self.assertEqual(os.listdir(self.out_dir), [os.path.basename(zip_path)])
        self.assertEqual(zip_path,
                         os.path.join(self.out_dir, "2026-08-30-portable-pack.zip"))

    def test_pack_verifies_clean(self):
        zip_path, _ = self._build()
        verdict, problems = PP.verify_pack(zip_path)
        self.assertEqual(verdict, "PASS", problems)
        self.assertEqual(problems, [])

    def test_lessons_member_present_only_when_given(self):
        zip_path, _ = self._build()
        with zipfile.ZipFile(zip_path) as zf:
            self.assertNotIn("04-LESSONS.json", zf.namelist())

        lesson_path = os.path.join(self.td.name, "lessons.json")
        write_json(lesson_path, [{"name": "x", "description": "y"}])
        zip_path2, _ = self._build(lesson_file=lesson_path,
                                   out_dir=os.path.join(self.td.name, "out2"))
        with zipfile.ZipFile(zip_path2) as zf:
            self.assertIn("04-LESSONS.json", zf.namelist())
            self.assertEqual(json.loads(zf.read("04-LESSONS.json")),
                             [{"name": "x", "description": "y"}])
        verdict, problems = PP.verify_pack(zip_path2)
        self.assertEqual(verdict, "PASS", problems)

    def test_state_carries_repo_head_shas(self):
        zip_path, _ = self._build()
        with zipfile.ZipFile(zip_path) as zf:
            state = json.loads(zf.read("05-STATE.json"))
        self.assertEqual(state["repos"]["."]["head"],
                         "deadbeefcafebabe0000000000000000000000")

    def test_no_data_when_a_collected_piece_errors(self):
        def erroring_collect(repos):
            return {"repos": {}, "sbe_tasks": {"error": "NO-DATA: nope"},
                    "day_plan": {}, "pull_requests": {}}
        zip_path, had_no_data = self._build(collect=erroring_collect)
        self.assertTrue(had_no_data)
        # the pack is still written; NO-DATA is not a failure to produce it
        self.assertTrue(os.path.isfile(zip_path))


class PortabilityProof(unittest.TestCase):
    """R25.4's own done-check, driven both forward and backward: unpacked
    outside every repo with HOME pointed elsewhere, then corrupted."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.roadmap_path = os.path.join(self.td.name, "roadmap.json")
        write_json(self.roadmap_path, valid_doc(
            [valid_row("R1", "DONE"), valid_row("R2", "OPEN"),
             valid_row("R3", "OPEN"), valid_row("R4", "DONE")]))
        self.repo = os.path.join(self.td.name, "fixture-repo")
        os.makedirs(self.repo)
        git_init(self.repo)
        self.out_dir = os.path.join(self.td.name, "out")
        self.zip_path, self.had_no_data = PP.build_pack(
            [self.repo], roadmap_path=self.roadmap_path,
            out_dir=self.out_dir, today="2026-08-30",
            collect=HC.collect_state)  # the real collector, against a real
                                       # (fixture) git repo: fast because the
                                       # repo has one commit.

    def test_unpacks_outside_every_repo_with_home_elsewhere_and_checks_out(self):
        extract_dir = tempfile.mkdtemp(prefix="portable-pack-extract-")
        self.addCleanup(lambda: __import__("shutil").rmtree(extract_dir,
                                                            ignore_errors=True))
        self.assertFalse(extract_dir.startswith(REPO_ROOT))
        self.assertFalse(extract_dir.startswith(self.repo))

        alien_home = tempfile.mkdtemp(prefix="portable-pack-alien-home-")
        self.addCleanup(lambda: __import__("shutil").rmtree(alien_home,
                                                            ignore_errors=True))
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = alien_home
        try:
            with zipfile.ZipFile(self.zip_path) as zf:
                zf.extractall(extract_dir)

            with open(os.path.join(extract_dir, "02-MEGA-PROMPT.md"),
                     encoding="utf-8") as f:
                mega = f.read()
            for line in mega.splitlines():
                if line.strip().startswith("git clone"):
                    continue
                self.assertIsNone(PP.PATH_PATTERN.search(line), line)

            board_path = os.path.join(extract_dir, "03-BOARD.html")
            self.assertGreater(os.path.getsize(board_path), 0)
            from html.parser import HTMLParser
            with open(board_path, encoding="utf-8") as f:
                HTMLParser().feed(f.read())  # must not raise

            with open(os.path.join(extract_dir, "05-STATE.json"),
                     encoding="utf-8") as f:
                state = json.load(f)
            heads = [r.get("head") for r in state["repos"].values()
                    if isinstance(r, dict) and r.get("head")]
            self.assertTrue(heads)
            self.assertEqual(len(heads[0]), 40)  # a real sha, not a placeholder

            with open(os.path.join(extract_dir, "01-START-HERE.md"),
                     encoding="utf-8") as f:
                start = f.read()
            self.assertIn("DONE: 2   OPEN: 2   (of 4 total rows)", start)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

        verdict, problems = PP.verify_pack(self.zip_path)
        self.assertEqual(verdict, "PASS", problems)

    def test_driven_backwards_a_corrupted_member_is_named(self):
        corrupt_zip = os.path.join(self.td.name, "corrupt.zip")
        with zipfile.ZipFile(self.zip_path) as src, \
             zipfile.ZipFile(corrupt_zip, "w") as dst:
            for item in src.namelist():
                data = src.read(item)
                if item == "05-STATE.json":
                    data = b"{not valid json"
                dst.writestr(item, data)

        verdict, problems = PP.verify_pack(corrupt_zip)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("05-STATE.json" in p for p in problems), problems)

    def test_a_missing_required_member_is_named(self):
        stripped_zip = os.path.join(self.td.name, "stripped.zip")
        with zipfile.ZipFile(self.zip_path) as src, \
             zipfile.ZipFile(stripped_zip, "w") as dst:
            for item in src.namelist():
                if item == "03-BOARD.html":
                    continue
                dst.writestr(item, src.read(item))

        verdict, problems = PP.verify_pack(stripped_zip)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("03-BOARD.html" in p for p in problems), problems)

    def test_a_mismatched_count_between_start_here_and_board_is_caught(self):
        mismatched_zip = os.path.join(self.td.name, "mismatched.zip")
        with zipfile.ZipFile(self.zip_path) as src, \
             zipfile.ZipFile(mismatched_zip, "w") as dst:
            for item in src.namelist():
                data = src.read(item)
                if item == "01-START-HERE.md":
                    data = data.decode("utf-8").replace(
                        "DONE: 2   OPEN: 2", "DONE: 9   OPEN: 9").encode("utf-8")
                dst.writestr(item, data)

        verdict, problems = PP.verify_pack(mismatched_zip)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("do not match" in p for p in problems), problems)

    def test_a_truncated_zip_is_no_data_not_a_silent_pass(self):
        truncated = os.path.join(self.td.name, "truncated.zip")
        with open(self.zip_path, "rb") as f:
            raw = f.read()
        with open(truncated, "wb") as f:
            f.write(raw[: len(raw) // 2])

        verdict, problems = PP.verify_pack(truncated)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(problems)

    def test_a_missing_zip_is_no_data(self):
        verdict, problems = PP.verify_pack(
            os.path.join(self.td.name, "does-not-exist.zip"))
        self.assertEqual(verdict, "NO-DATA")


class RealCollectAgainstThisWorktree(unittest.TestCase):
    """The task's own instruction: at least one test runs the REAL collect
    against this worktree, not a fixture. Uses the real roadmap (already
    known to validate: scripts/test_gen_readiness_board.py and
    scripts/gen_readiness_board.py --check both cover that separately)."""

    def test_real_run_against_this_worktree_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            zip_path, had_no_data = PP.build_pack(
                [REPO_ROOT], out_dir=td, today="2026-08-30")
            verdict, problems = PP.verify_pack(zip_path)
            self.assertEqual(verdict, "PASS", problems)
            # pull_requests may be NO-DATA on a machine without `gh`; that is
            # allowed and reported, never silently upgraded to a pass.
            with zipfile.ZipFile(zip_path) as zf:
                state = json.loads(zf.read("05-STATE.json"))
            self.assertIn(REPO_ROOT, state["repos"])
            self.assertEqual(len(state["repos"][REPO_ROOT]["head"]), 40)


class CliMain(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args),
                              capture_output=True, text=True, timeout=120,
                              cwd=REPO_ROOT)

    def test_verify_missing_zip_exits_2_and_says_no_data(self):
        out = self._run("--verify", "/no/such/pack.zip")
        self.assertEqual(out.returncode, 2)
        self.assertIn("NO-DATA", out.stdout)

    def test_build_then_verify_round_trip_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "out")
            build = self._run("--repo", REPO_ROOT, "--out-dir", out_dir,
                              "--date", "2026-08-30")
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            zip_path = os.path.join(out_dir, "2026-08-30-portable-pack.zip")
            self.assertTrue(os.path.isfile(zip_path))
            verify = self._run("--verify", zip_path)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            self.assertIn("PASS", verify.stdout)


if __name__ == "__main__":
    unittest.main()
