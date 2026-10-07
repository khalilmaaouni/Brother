#!/usr/bin/env python3
"""native_adapter.py turns a checkout a Claude native worker edited into the build JSON the grader already accepts, and
refuses, writing nothing, every checkout it cannot turn into one faithfully (owner order 2026-10-02, "wire the
Claude-native worker into the loop", PLAN step 1 of ~/Documents/BrotherModeUp-handovers/2026-10-01-brother-1.1.0-night-pack/
09-NATIVE-WORKER-HANDOVER.md).

ONE CONDITION PER FIXTURE: each refusal case starts from the good checkout and breaks exactly one thing, so only the guard
it names can refuse it. THE ENTRY POINT: every case runs main(argv) as the worker runs it and reads its exit code and
whether the build file exists; the helper is never tested alone.
Run: python3 -B scripts/loop/test_native_adapter.py"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import native_adapter as NA  # noqa: E402
import grade_build as G  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
MUTS = [{"name": "m%d" % i, "path": "pkg/mod.py", "find": "return %d" % i, "replace": "return -1", "caught_by": "t"}
        for i in (1, 2, 3)]
MOD_NEW = "def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def git(wd, *args):
    subprocess.run(["git"] + list(args), cwd=wd, env=GIT_ENV, check=True, capture_output=True)


class Checkout(unittest.TestCase):
    """A base commit holding pkg/mod.py and pkg/empty.py, then the GOOD worker result: mod.py edited, a new test file,
    and the three .brother files. Each test breaks one thing on top of it."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="native-adapter-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.wd = os.path.join(self.root, "wt")
        os.makedirs(os.path.join(self.wd, "pkg"))
        git(self.wd, "init", "-q")
        self.write("pkg/mod.py", "def a():\n    return 0\n")
        self.write("pkg/empty.py", "")
        self.write(".gitignore", ".brother/\n")
        git(self.wd, "add", "-A")
        git(self.wd, "commit", "-q", "-m", "base")
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.wd, env=GIT_ENV, capture_output=True,
                                   text=True, check=True).stdout.strip()
        self.write("pkg/mod.py", MOD_NEW)
        self.write("pkg/test_mod.py", "import unittest\n")
        self.write(".brother/done_check.txt", "python3 -B -m unittest pkg.test_mod\n")
        self.write(".brother/mutations.json", json.dumps(MUTS))
        self.write(".brother/notes.json", json.dumps({"callers_checked": ["pkg/mod.py"], "unknowns": []}))
        self.out = os.path.join(self.root, "build.json")

    def write(self, rel, text):
        p = os.path.join(self.wd, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_main(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = NA.main([self.wd, self.base, self.out])
        return rc, buf.getvalue()

    def assertRefused(self, why):
        rc, said = self.run_main()
        self.assertEqual(rc, 2, said)
        self.assertIn("ADAPTER REFUSED", said)
        self.assertIn(why, said)
        self.assertFalse(os.path.exists(self.out), "a refused checkout must write no build")


def apply_edits(text, edits):
    """The grader's own rule (grade_build.replace_once: exactly one match, overlaps counted), applied in order."""
    for e in edits:
        nxt = G.replace_once(text, e["find"], e["replace"])
        assert nxt is not None, e["find"]
        text = nxt
    return text


BIG = "".join("def f%d():\n    return %d\n\n" % (i, i) for i in range(200))


class Hunks(unittest.TestCase):
    def test_a_one_line_change_in_a_big_file_is_a_small_find(self):
        after = BIG.replace("return 57\n", "return 570\n")
        pairs = NA.hunks(BIG, after)
        self.assertEqual(len(pairs), 1)
        self.assertLess(len(pairs[0][0]), 40)
        self.assertEqual(apply_edits(BIG, [{"find": f, "replace": r} for f, r in pairs]), after)

    def test_a_landing_elsewhere_in_the_file_leaves_the_build_applicable(self):
        after = BIG.replace("return 57\n", "return 570\n")
        moved = BIG.replace("return 150\n", "return 1500\n")   # another lane landed here meanwhile
        edits = [{"find": f, "replace": r} for f, r in NA.hunks(BIG, after)]
        self.assertEqual(apply_edits(moved, edits), moved.replace("return 57\n", "return 570\n"))
        self.assertEqual(moved.count(BIG), 0, "a whole file find would have been refused")

    def test_a_repeated_line_gets_just_enough_context_to_be_unique(self):
        before = "x = 1\nA\nx = 1\nB\n"
        after = "x = 1\nA\nx = 2\nB\n"
        pairs = NA.hunks(before, after)
        self.assertIsNotNone(pairs)
        self.assertEqual(len(pairs), 1)
        self.assertLess(len(pairs[0][0]), len(before), "context, not the whole file")
        self.assertEqual(apply_edits(before, [{"find": f, "replace": r} for f, r in pairs]), after)

    def test_a_huge_repetitive_file_finishes_fast(self):
        import time
        before = "x = 1\n" * 20000
        after = before[:6 * 10000] + "x = 2\n" + before[6 * 10001:]
        t0 = time.time()
        pairs = NA.hunks(before, after)
        self.assertLess(time.time() - t0, 10.0)
        self.assertIsNone(pairs, "past the line cap the whole file is the find")

    def test_a_file_at_the_cap_still_gets_hunks(self):
        before = "".join("v%d = %d\n" % (i, i) for i in range(NA.HUNK_LINES_MAX))
        after = before.replace("v77 = 77\n", "v77 = 770\n")
        pairs = NA.hunks(before, after)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(apply_edits(before, [{"find": f, "replace": r} for f, r in pairs]), after)

    def test_context_past_the_cap_falls_back_to_the_whole_file(self):
        block = "".join("v%d\n" % i for i in range(70))
        lines = (block * 2).splitlines(True)
        lines[105] = "changed\n"   # every window under 36 lines here also appears in the first copy
        self.assertIsNone(NA.hunks(block * 2, "".join(lines)))

    def test_a_periodic_block_gets_a_find_the_grader_accepts(self):
        # fifth review 2026-10-02: "ab\nab\n" inside "ab\nab\nab\n" is one str.count match but two real ones. These three
        # pairs were found by fuzzing the old adapter, which emitted a find the grader refuses for each; a unique window
        # exists in every one, so hunks must find it rather than fall back to the whole file
        for b, a in (("ab\nab\nab\nab\nc\n", "Z\nab\nab\nab\nc\n"),
                     ("ab\nab\nab\nab\nab\nc\nc\nc\n", "ab\nab\nab\nab\nab\nab\nc\nc\n"),
                     ("c\nab\nab\nab\n", "c\nab\nab\nab\nab\n")):
            pairs = NA.hunks(b, a)
            self.assertIsNotNone(pairs, (b, a))
            for f, _ in pairs:
                self.assertEqual(len(G.occurrences(b, f)), 1, (b, f))
            self.assertEqual(apply_edits(b, [{"find": f, "replace": r} for f, r in pairs]), a, (b, a))

    def test_the_replay_proof_applies_as_the_grader_does(self):
        # found by fuzzing: each find is unique in the ORIGINAL, but a later one overlaps itself in the text the earlier
        # pairs leave; str.count passed it, the grader refuses it. Hunks must give a build the grader applies, or None
        for b, a in (("c\nb\nb\n", "c\nab\nb\n"), ("ab\nc\nab\nb\nab\nab\nab\n", "ab\nc\nab\nab\nab\nab\nab\n")):
            pairs = NA.hunks(b, a)
            if pairs is not None:
                self.assertEqual(apply_edits(b, [{"find": f, "replace": r} for f, r in pairs]), a, (b, a))

    def test_equal_texts_need_no_pair(self):
        self.assertEqual(NA.hunks(BIG, BIG), [])

    def test_every_answer_replays_or_is_refused(self):
        import random
        rnd = random.Random(7)
        for _ in range(300):
            a = [rnd.choice(["x\n", "y\n", "z\n", "x\n"]) for _ in range(rnd.randint(1, 12))]
            b = list(a)
            for _ in range(rnd.randint(1, 4)):
                op = rnd.choice(("ins", "del", "sub"))
                i = rnd.randint(0, len(b))
                if op == "ins": b.insert(i, rnd.choice(["x\n", "w\n"]))
                elif b and op == "del": del b[min(i, len(b) - 1)]
                elif b: b[min(i, len(b) - 1)] = "w\n"
            before, after = "".join(a), "".join(b)
            pairs = NA.hunks(before, after)
            if pairs is not None:
                self.assertEqual(apply_edits(before, [{"find": f, "replace": r} for f, r in pairs]), after, (before, after))


class GoodBuild(Checkout):
    def test_good_checkout_becomes_the_grader_shape(self):
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        build = load(self.out)
        self.assertEqual(build["done_check"], "python3 -B -m unittest pkg.test_mod")
        self.assertEqual(apply_edits("def a():\n    return 0\n", build["edits"]), MOD_NEW)
        self.assertEqual([e["path"] for e in build["edits"]], ["pkg/mod.py"])
        self.assertEqual(build["tests"], [{"path": "pkg/test_mod.py", "new_file_content": "import unittest\n"}])
        self.assertEqual(len(build["mutations"]), 3)
        self.assertEqual(build["callers_checked"], ["pkg/mod.py"])

    def test_the_loops_status_lock_never_rides_in_a_build(self):
        # 2026-10-03: the loop's STATUS lock lands in a seat (empty, untracked), every native build carried it as a new
        # file, and the pool held D2.6 behind FX-31.5 on "TOUCH: shares .status.lock"
        self.write(".status.lock", "")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        build = load(self.out)
        self.assertNotIn(".status.lock", [e["path"] for e in build["edits"] + build["tests"]])

    def test_empty_replace_is_a_mutation(self):
        muts = [dict(m) for m in MUTS]
        muts[0]["replace"] = ""   # deleting a guard is a mutation
        self.write(".brother/mutations.json", json.dumps(muts))
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)

    def test_notes_are_optional(self):
        os.remove(os.path.join(self.wd, ".brother/notes.json"))
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        self.assertEqual(load(self.out)["unknowns"], [])

    def test_a_file_name_with_a_space_survives(self):
        self.write("pkg/two words.py", "x = 1\n")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        self.assertIn("pkg/two words.py", [e["path"] for e in load(self.out)["edits"]])

    def test_brother_files_never_ride_in_the_build(self):
        git(self.wd, "add", "-f", ".brother")
        git(self.wd, "commit", "-q", "-m", "worker committed its notes")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        build = load(self.out)
        self.assertEqual([p for p in (i["path"] for i in build["edits"] + build["tests"]) if p.startswith(".brother")], [])

    def test_the_session_scratch_never_rides_in_the_build(self):
        self.write(".tmp/tmpabc/x.py", "x = 1\n")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        build = load(self.out)
        self.assertEqual([p for p in (i["path"] for i in build["edits"] + build["tests"]) if p.startswith(".tmp")], [])

    def test_a_checkout_hook_never_runs_out_here(self):
        marker = os.path.join(self.root, "hook-ran")
        hook = os.path.join(self.root, "fsmon.sh")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\n" % marker)
        os.chmod(hook, 0o755)
        git(self.wd, "config", "core.fsmonitor", hook)
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        self.assertFalse(os.path.exists(marker), "the checkout's fsmonitor hook ran outside the sandbox")

    def test_a_committed_change_is_carried_too(self):
        git(self.wd, "add", "pkg/mod.py")
        git(self.wd, "commit", "-q", "-m", "worker committed")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        self.assertIn("pkg/mod.py", [e["path"] for e in load(self.out)["edits"]])


class Refusals(Checkout):
    def test_fewer_than_three_mutations(self):
        self.write(".brother/mutations.json", json.dumps(MUTS[:2]))
        self.assertRefused("3 or more mutations")

    def test_mutations_not_a_list(self):
        self.write(".brother/mutations.json", json.dumps({"name": "x", "path": "y", "find": "z"}))   # three keys
        self.assertRefused("3 or more mutations")

    def test_notes_not_an_object(self):
        self.write(".brother/notes.json", json.dumps(["pkg/mod.py"]))
        self.assertRefused("an object is required")

    def test_not_a_git_checkout(self):
        self.wd = os.path.join(self.root, "plain")
        os.makedirs(self.wd)
        self.assertRefused("git status failed")

    def test_unknown_base(self):
        self.base = "0" * 40
        self.assertRefused("git diff failed")

    def test_deleted_file(self):
        os.remove(os.path.join(self.wd, "pkg/empty.py"))
        self.assertRefused("deleted")

    def test_deleted_in_a_commit(self):
        git(self.wd, "rm", "-q", "pkg/empty.py")
        git(self.wd, "commit", "-q", "-m", "worker deleted")
        self.assertRefused("deleted")

    def test_renamed_file(self):
        git(self.wd, "mv", "pkg/empty.py", "pkg/renamed.py")
        self.assertRefused("renamed")

    def test_edited_file_empty_at_base(self):
        self.write("pkg/empty.py", "x = 1\n")
        self.assertRefused("empty at base")

    def test_find_not_unique(self):
        muts = [dict(m) for m in MUTS]
        muts[0]["find"] = "    return"   # occurs three times in the edited file
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("needs exactly once")

    def test_find_matching_twice_only_when_overlaps_count(self):
        # "abab" inside "ababab": str.count says 1, the grader says 2 (fifth review 2026-10-02)
        self.write("pkg/mod.py", MOD_NEW + "zz = 'ababab'\n")
        muts = [dict(m) for m in MUTS]
        muts[0]["find"] = "abab"
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("needs exactly once")

    # sixth review 2026-10-02: the adapter runs OUTSIDE the sandbox, so it may read only regular files in the seat
    def outside_secret(self):
        p = os.path.join(self.root, "outside-secret.txt")
        with open(p, "w") as fh:
            fh.write("SECRET-MARKER\n")
        return p

    def test_a_new_symlink_is_refused_and_its_target_never_read(self):
        os.symlink(self.outside_secret(), os.path.join(self.wd, "pkg", "fixture.txt"))
        self.assertRefused("symlink")

    def test_a_tracked_file_turned_symlink_is_refused(self):
        os.remove(os.path.join(self.wd, "pkg", "mod.py"))
        os.symlink(self.outside_secret(), os.path.join(self.wd, "pkg", "mod.py"))
        self.assertRefused("changed type")

    def test_a_fifo_is_refused_without_blocking(self):
        import threading
        os.mkfifo(os.path.join(self.wd, "pkg", "pipe.txt"))   # git skips an untracked FIFO; a mutation path opens it
        muts = [dict(m) for m in MUTS]
        muts[0]["path"] = "pkg/pipe.txt"
        self.write(".brother/mutations.json", json.dumps(muts))
        box = []
        t = threading.Thread(target=lambda: box.append(self.run_main()), daemon=True)
        t.start(); t.join(10)
        self.assertTrue(box, "the adapter blocked on a FIFO")
        rc, said = box[0]
        self.assertEqual(rc, 2, said)
        self.assertIn("not a regular file", said)

    def test_a_mutation_path_outside_the_seat_is_refused(self):
        muts = [dict(m) for m in MUTS]
        muts[0]["path"] = self.outside_secret()
        muts[0]["find"] = "SECRET-MARKER"
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("escapes the tree")

    def test_a_mutation_path_through_a_symlink_is_refused(self):
        os.symlink(self.outside_secret(), os.path.join(self.wd, "pkg", "ignored-link.txt"))
        with open(os.path.join(self.wd, ".git", "info", "exclude"), "a") as fh:
            fh.write("pkg/ignored-link.txt\n")
        muts = [dict(m) for m in MUTS]
        muts[0]["path"], muts[0]["find"] = "pkg/ignored-link.txt", "SECRET-MARKER"
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("symlink")

    def test_find_absent(self):
        muts = [dict(m) for m in MUTS]
        muts[0]["find"] = "return 99"
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("needs exactly once")

    def test_mutation_lacks_a_field(self):
        muts = [dict(m) for m in MUTS]
        del muts[1]["name"]
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("lacks name")

    def test_mutation_replace_not_a_string(self):
        muts = [dict(m) for m in MUTS]
        muts[2]["replace"] = None
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("lacks name")

    def test_mutation_names_a_missing_file(self):
        muts = [dict(m) for m in MUTS]
        muts[0]["path"] = "pkg/nowhere.py"
        self.write(".brother/mutations.json", json.dumps(muts))
        self.assertRefused("does not exist")

    def test_no_change(self):
        git(self.wd, "checkout", "-q", "--", "pkg/mod.py")
        os.remove(os.path.join(self.wd, "pkg/test_mod.py"))
        self.assertRefused("no file changed")

    def test_missing_done_check(self):
        os.remove(os.path.join(self.wd, ".brother/done_check.txt"))
        self.assertRefused("unreadable .brother file")

    def test_two_command_done_check(self):
        self.write(".brother/done_check.txt", "python3 a.py\npython3 b.py\n")
        self.assertRefused("exactly one command")

    def test_blank_done_check(self):
        self.write(".brother/done_check.txt", "  \n")
        self.assertRefused("exactly one command")

    def test_mutations_not_json(self):
        self.write(".brother/mutations.json", "[{")
        self.assertRefused("unreadable .brother file")

    def test_notes_not_json(self):
        self.write(".brother/notes.json", "{")
        self.assertRefused("notes.json is not JSON")

    def test_a_new_file_with_carriage_returns_rides_as_it_is(self):
        with open(os.path.join(self.wd, "pkg/crlf.csv"), "wb") as fh:
            fh.write(b"a,b\r\n1,2\r\n")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        self.assertIn({"path": "pkg/crlf.csv", "new_file_content": "a,b\r\n1,2\r\n"}, load(self.out)["edits"])

    def test_an_edit_to_a_carriage_return_file_replays_exactly(self):
        with open(os.path.join(self.root, "crlf"), "wb") as fh:
            fh.write(b"a\r\n")
        os.makedirs(os.path.join(self.wd, "data"), exist_ok=True)
        with open(os.path.join(self.wd, "data/t.txt"), "wb") as fh:
            fh.write(b"a = 1\r\nb = 2\r\n")
        git(self.wd, "add", "data/t.txt")
        git(self.wd, "commit", "-q", "-m", "crlf base")
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.wd, env=GIT_ENV, capture_output=True,
                                   text=True, check=True).stdout.strip()
        with open(os.path.join(self.wd, "data/t.txt"), "wb") as fh:
            fh.write(b"a = 1\r\nb = 3\r\n")
        rc, said = self.run_main()
        self.assertEqual(rc, 0, said)
        mine = [e for e in load(self.out)["edits"] if e["path"] == "data/t.txt"]
        self.assertEqual(apply_edits("a = 1\r\nb = 2\r\n", mine), "a = 1\r\nb = 3\r\n")

    def test_a_binary_file(self):
        with open(os.path.join(self.wd, "pkg/blob.bin"), "wb") as fh:
            fh.write(b"\xff\xfe\x00bad")
        self.assertRefused("not utf-8")

    def test_not_a_checkout(self):
        self.wd = os.path.join(self.root, "nowhere")
        self.assertRefused("git")

    def test_wrong_argument_count(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(NA.main([self.wd]), 2)


if __name__ == "__main__":
    unittest.main()
