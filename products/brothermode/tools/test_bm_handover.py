#!/usr/bin/env python3
"""Suite for tools/bm_handover.py: the baton ceremony tool (spec
docs/superpowers/specs/2026-08-11-baton-ceremony-design.md, sections 4 and
8, V1 through V5).

Every test drives a throwaway project root under a temporary directory, with
BROTHERMODE_ROOT pointed at it, so nothing here touches the real project's
own store or the real ~/Documents/BrotherModeUp-handovers. The zip
destination is likewise redirected per-test through
BROTHERMODE_HANDOVERS_DIR, an env override bm_handover.py itself reads at
call time (mirroring the BROTHERMODE_VAULT idiom tools/bm_autosave.py and
tools/bm_ledger.py already use).

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same technique tools/bm_packs.py
    and tools/bm_stall.py use for tools/bm_store.py."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_optional(name):
    """None instead of an exception, so every test in this file fails on
    its own with a message naming the missing file (the RED state before
    tools/bm_handover.py exists), rather than one collection-time error
    collapsing the whole suite."""
    try:
        return _load(name)
    except Exception:  # sbe: allow-silent documented above: None lets every test in this file fail on its own naming the missing module, rather than one collection-time error
        return None


bh = _load_optional("bm_handover")
bs = bh.bs if bh is not None else _load("bm_store")


class HandoverCase(unittest.TestCase):
    """A throwaway project root and a real bm_store.Store under it, with
    BROTHERMODE_ROOT and BROTHERMODE_HANDOVERS_DIR pointed there so nothing
    here can reach a real project or a real founder home directory."""

    ENV_KEYS = ("BROTHERMODE_ROOT", "BROTHERMODE_HANDOVERS_DIR",
                "BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID")

    def setUp(self):
        self.assertIsNotNone(
            bh, "tools/bm_handover.py does not exist yet, so nothing in "
                "this file can run. That is the failing-first (RED) state "
                "the baton ceremony spec's section 8 asks this class to "
                "reproduce.")
        self.tmp = tempfile.mkdtemp(prefix="bm-handover-")
        self.root = os.path.join(self.tmp, "project")
        self.handovers_dir = os.path.join(self.tmp, "handovers")
        os.makedirs(self.root)
        self._env_backup = {k: os.environ.get(k) for k in self.ENV_KEYS}
        os.environ["BROTHERMODE_ROOT"] = self.root
        os.environ["BROTHERMODE_HANDOVERS_DIR"] = self.handovers_dir
        self.store = bs.Store(self.root)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:  # sbe: allow-silent tearDown best-effort store.close(); the test's own assertions already ran before this cleanup
            pass
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- driving the CLI in process ------------------------------------

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = bh.main(list(argv))
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return code, out.getvalue(), err.getvalue()

    # -- seeding ----------------------------------------------------------

    def claim(self, name, session_id="test-session", files=None):
        return self.store.claim(
            name=name, lifetime="persistent",
            files=files or ["%s.py" % name], objective="carry %s" % name,
            owner="tester", session_id=session_id)

    def park(self, record, session_id="test-session"):
        return self.store.transition(
            record.lifecycle_uuid, record.version, "parked",
            session_id=session_id, note="parked by test",
            handover_heading="handing off %s" % record.name)

    def read(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()

    def write(self, path, text):
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def fill_human_block(self, path, body):
        """Replace the single human block in a generated pack page with
        `body`, the same shape a person editing the file by hand would
        produce."""
        text = self.read(path)
        begin, end = bs.HUMAN_BLOCK_BEGIN, bs.HUMAN_BLOCK_END
        pre, _, rest = text.partition(begin)
        _mid, sep, post = rest.partition(end)
        self.assertTrue(sep, "no human block found in %s" % path)
        self.write(path, pre + begin + "\n" + body + "\n" + end + post)

    def fill_all_human_blocks(self, path, body):
        """Replace EVERY human block in a generated pack page with `body`.
        07-NEXT-SESSION-PROMPT.md carries len(bh.PROMPT_SLOTS) separate
        blocks (goal, decisions, work list, frozen/blocked) rather than
        the single block every other page has; this is that page's
        equivalent of fill_human_block."""
        text = self.read(path)
        begin, end = bs.HUMAN_BLOCK_BEGIN, bs.HUMAN_BLOCK_END
        out, rest, filled = [], text, 0
        while True:
            pre, sep, tail = rest.partition(begin)
            out.append(pre)
            if not sep:
                break
            _mid, sep2, tail2 = tail.partition(end)
            self.assertTrue(sep2, "unterminated human block in %s" % path)
            out.append(begin + "\n" + body + "\n" + end)
            filled += 1
            rest = tail2
        self.assertTrue(filled, "no human block found in %s" % path)
        self.write(path, "".join(out))

    def fill_pack(self, pack_dir, status_line="FINISHED: all clear."):
        """Fill every narrative slot in a freshly generated pack with real
        text, so no FILL-BY-HAND marker survives. 06-CLOSE-REPORT.md's
        block opens with `status_line`. 07-NEXT-SESSION-PROMPT.md's four
        slots are each filled the same way, via fill_all_human_blocks."""
        for name in bh.PACK_FILES:
            path = os.path.join(pack_dir, name)
            if name == "06-CLOSE-REPORT.md":
                self.fill_human_block(
                    path, status_line + "\nNothing else outstanding.")
            elif name == bh.PROMPT_PAGE:
                self.fill_all_human_blocks(
                    path, "Filled in by the test suite: nothing left to "
                          "say about this slot.")
            else:
                self.fill_human_block(
                    path, "Filled in by the test suite: nothing left to "
                          "say about %s." % name)

    def _real_git_repo(self, add_paths=()):
        """A REAL git repository at self.root, with `add_paths` staged.

        A real index rather than a hand-built one, deliberately: the check
        under test parses git's own binary index format in pure Python, and
        a fixture that fakes that format tests the fixture. The first draft
        of this test did fake it, the parser correctly refused to read it,
        and the test failed for a reason that had nothing to do with the
        behaviour it was written to pin.

        Lives on the shared base class, not just TestVerifyClose, because
        TestZip's own defect two regression test (M19 outside review) needs
        a real committed pack too: cmd_zip's board refresh can go stale
        against a commit exactly as easily as verify-close's untracked
        check can be defeated by one, and both need the real index to
        prove it."""
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["HOME"] = self.tmp
        def git(*args):
            return subprocess.run(("git",) + args, cwd=self.root, env=env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=30)
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        # The store refuses to open inside a git repository that does not
        # ignore .brothermode, because the raw store carries founder text in
        # cleartext and would be one `git add -A` from being committed. That
        # refusal is correct and this fixture satisfies it the way the real
        # product does, by writing the exclude rule, rather than by setting
        # the skip flag and testing a shape no user runs.
        exclude = os.path.join(self.root, ".git", "info", "exclude")
        if not os.path.isdir(os.path.dirname(exclude)):
            os.makedirs(os.path.dirname(exclude))
        with io.open(exclude, "a", encoding="utf-8") as fh:
            fh.write("\n.brothermode\n")
        for rel in add_paths:
            git("add", rel)
        return git


# ---------------------------------------------------------------------------
# V1 + V2: skeleton
# ---------------------------------------------------------------------------

class TestSkeleton(HandoverCase):

    def test_v1_skeleton_writes_all_seven_files_prefilled_and_marked(self):
        r1 = self.claim("fence-alpha")
        r2 = self.claim("fence-beta")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        for name in bh.PACK_FILES:
            path = os.path.join(out_dir, name)
            self.assertTrue(os.path.isfile(path), "missing %s" % name)
            text = self.read(path)
            self.assertIn(bh.FILL_BY_HAND, text,
                          "%s has no FILL-BY-HAND marker" % name)
        handover_text = self.read(os.path.join(out_dir, "01-HANDOVER.md"))
        self.assertIn(r1.name, handover_text)
        self.assertIn(r1.lifecycle_uuid[:8], handover_text)
        self.assertIn(r2.name, handover_text)
        self.assertIn(r2.lifecycle_uuid[:8], handover_text)

    def test_v2_rerun_over_filled_pack_preserves_human_bytes(self):
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, _out, _err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code)
        target = os.path.join(out_dir, "02-LEARNINGS-AND-MISTAKES.md")
        sentinel = ("A real lesson a human wrote by hand: the fence sweep "
                    "found nothing new twice in a row before we stopped.")
        self.fill_human_block(target, sentinel)
        filled_text = self.read(target)
        self.assertNotIn(bh.FILL_BY_HAND, filled_text)
        # A second record now exists in the store, so the STRUCTURAL part of
        # every page changes on regeneration; the human paragraph must not.
        self.claim("fence-beta")
        code, _out, _err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code)
        regenerated = self.read(target)
        self.assertIn(sentinel, regenerated,
                      "the human paragraph did not survive regeneration (I10)")
        self.assertNotIn(bh.FILL_BY_HAND, regenerated,
                         "regeneration reintroduced the FILL-BY-HAND default "
                         "over a block a human had already filled")
        handover_text = self.read(os.path.join(out_dir, "01-HANDOVER.md"))
        self.assertIn("fence-beta", handover_text,
                      "the structural section did not pick up the new record")

    def test_v6_skeleton_writes_the_eighth_prompt_page_and_names_it_in_read_order(self):
        """Pre-fix: PACK_FILES held only the seven narrative/close files, so
        skeleton never wrote 07-NEXT-SESSION-PROMPT.md at all, and
        00-READ-ME-FIRST.md's own read-order sentence never named an
        eighth file to read."""
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        prompt_path = os.path.join(out_dir, bh.PROMPT_PAGE)
        self.assertTrue(os.path.isfile(prompt_path),
                        "07-NEXT-SESSION-PROMPT.md was not written")
        prompt_text = self.read(prompt_path)
        for label, _hint in bh.PROMPT_SLOTS:
            self.assertIn(label, prompt_text,
                          "slot %r missing from the generated page" % label)
        read_me = self.read(os.path.join(out_dir, "00-READ-ME-FIRST.md"))
        self.assertIn(bh.PROMPT_PAGE, read_me,
                      "the read-order sentence does not name the eighth page")

    def test_v7_regeneration_preserves_all_four_prompt_slots(self):
        """Pre-fix: there was no eighth page at all, so nothing preserved a
        hand-filled slot across a regeneration; the multi-block region this
        page carries (four separate human blocks under one NOTES_HEADING)
        is also a shape I10's preserve-verbatim logic had never been
        exercised against before this page existed."""
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, _out, _err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code)
        target = os.path.join(out_dir, bh.PROMPT_PAGE)
        sentinel = "A real goal a human wrote by hand: ship the A7 lane."
        text = self.read(target)
        begin, end = bs.HUMAN_BLOCK_BEGIN, bs.HUMAN_BLOCK_END
        pre, _, rest = text.partition(begin)
        _mid, sep, post = rest.partition(end)
        self.assertTrue(sep, "no human block found in %s" % target)
        self.write(target, pre + begin + "\n" + sentinel + "\n" + end + post)
        # A second record now exists, changing the STRUCTURAL part of every
        # page; the human paragraph in the FIRST slot must not move.
        self.claim("fence-beta")
        code, _out, _err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code)
        regenerated = self.read(target)
        self.assertIn(sentinel, regenerated,
                      "the first prompt slot's human text did not survive "
                      "regeneration")
        # And the OTHER three slots must still each carry their own
        # FILL-BY-HAND default: this page has four blocks, not one, and
        # filling one must not disturb the rest.
        self.assertEqual(
            regenerated.count(bh.FILL_BY_HAND), len(bh.PROMPT_SLOTS) - 1,
            "filling one slot changed how many of the other slots still "
            "carry their own default text")

    def test_m19_defect_one_skeleton_refuses_a_destination_symlink_too(
            self):
        """Orchestrator follow-up to the M19 outside review: the same
        unguarded shutil.copy2 shape cmd_zip had also lived in
        cmd_skeleton's own board copy, and it is just as reachable, since
        re-running skeleton over an EXISTING pack directory is ordinary,
        not exotic (test_v2 above does exactly that, to prove human text
        survives regeneration). Plant a symlink at the skeleton
        destination pointing at an external sentinel file, then re-run
        skeleton over the same pack, the same technique
        test_m19_defect_one_zip_refuses_a_destination_symlink_before_write
        (TestZip) uses against cmd_zip.

        Asserting only that an exception was raised would have PASSED
        against the broken code too, since the broken code also raised,
        just too late. The assertion that actually pins the fix is the
        external file's content, unchanged."""
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>the live board</html>")
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, _out, _err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code)
        board_in_pack = os.path.join(out_dir, "GANTT.html")
        self.assertTrue(os.path.isfile(board_in_pack))

        outside = os.path.join(self.tmp, "external-board-skeleton.html")
        sentinel = "EXTERNAL FILE BEFORE ANYTHING RAN. DO NOT TOUCH."
        self.write(outside, sentinel)
        os.remove(board_in_pack)
        os.symlink(outside, board_in_pack)

        # A second record forces a real regeneration (the same nudge
        # test_v2 above uses), and the board copy step runs again over the
        # symlink now sitting where GANTT.html used to be.
        self.claim("fence-beta")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("symlink", (out + err).lower(),
                      "expected the containment refusal's own message, "
                      "naming the symlink, not some other failure")

        self.assertEqual(
            sentinel, self.read(outside),
            "cmd_skeleton wrote through the destination symlink before "
            "refusing: the external file was overwritten with the live "
            "board's HTML")


# ---------------------------------------------------------------------------
# V3: verify-close
# ---------------------------------------------------------------------------

class TestVerifyClose(HandoverCase):

    def _fresh_pack(self, slot="closeit"):
        out_dir = os.path.join(self.root, "docs", "handover",
                               "2026-08-11-%s" % slot)
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-11", "--slot", slot)
        self.assertEqual(0, code)
        return out_dir

    def test_v3a_fails_on_surviving_fill_by_hand(self):
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code)
        self.assertIn("FAIL", out)
        self.assertIn(bh.FILL_BY_HAND, out)
        self.assertIn("00-READ-ME-FIRST.md", out)

    def test_v3b_fails_on_missing_finished_or_unfinished_line(self):
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir, status_line="still wrapping up, no verdict yet")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code)
        self.assertIn("FAIL", out)
        self.assertIn("FINISHED", out)
        self.assertIn("UNFINISHED", out)

    def test_v3c_fails_when_pack_is_newer_than_the_newest_zip(self):
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("zip", out.lower())

    def test_v3d_fails_when_session_still_owns_unparked_records(self):
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn(rec.lifecycle_uuid[:8], out)

    def test_v3e_passes_when_clean(self):
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def test_prompt_page_unfilled_slot_fails_naming_the_slot(self):
        """CHANGE 2: an unfilled 07-NEXT-SESSION-PROMPT.md slot must FAIL,
        naming exactly which slot, the same courtesy check 2 already gives
        the close report's status line. Pre-fix this page did not exist at
        all (PACK_FILES held seven files), so nothing could ever refuse
        it."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        # Unfill ONE slot only: leave the other three filled, so a FAIL
        # here can only come from the one slot still holding FILL-BY-HAND,
        # not from a confound like "no human block at all".
        target = os.path.join(pack_dir, bh.PROMPT_PAGE)
        text = self.read(target)
        begin, end = bs.HUMAN_BLOCK_BEGIN, bs.HUMAN_BLOCK_END
        pre, _, rest = text.partition(begin)
        _mid, sep, post = rest.partition(end)
        self.assertTrue(sep)
        self.write(target, pre + begin + "\n" + bh.FILL_BY_HAND
                  + ": still not written.\n" + end + post)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn(bh.PROMPT_PAGE, out)
        self.assertIn(bh.PROMPT_SLOTS[0][0], out,
                      "the FAIL does not name which slot is unfilled")

    def test_prompt_page_fully_filled_passes(self):
        """The other half: once every slot (and everything else) is
        filled, the pack passes, same as any other clean close."""
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _o, _e = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def test_v3f_no_store_is_no_data_never_pass(self):
        empty_root = os.path.join(self.tmp, "no-store-project")
        os.makedirs(empty_root)
        os.environ["BROTHERMODE_ROOT"] = empty_root
        try:
            code, out, _err = self.run_cli("verify-close")
        finally:
            os.environ["BROTHERMODE_ROOT"] = self.root
        self.assertNotEqual(0, code)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    # -- REFUTE-2026-08-11.md findings, one RED test per finding id --------

    def test_f1_prescribed_invocation_with_no_session_still_checks_ownership(self):
        """A2a, the headline finding. Section 3 step 7's own prescribed
        command is `verify-close` with no --session at all; that used to
        skip check 4 outright and PASS over a live unparked record. Now it
        falls back to the store's own notion of the current session
        (BM_FENCE_SESSION_ID, the same env var bm_store.py's own CLI
        session resolution reads first)."""
        os.environ["BM_FENCE_SESSION_ID"] = "closing-session"
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn(rec.lifecycle_uuid[:8], out)

    def test_f1_no_session_determinable_is_no_data_never_pass(self):
        """The other half of F1's fix: when neither --session nor either
        env var can name a session, the ownership check cannot run at
        all, and the verdict must be NO-DATA, never a silent PASS."""
        os.environ.pop("BM_FENCE_SESSION_ID", None)
        os.environ.pop("CLAUDE_SESSION_ID", None)
        self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertNotEqual(0, code, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_m10_zero_claimed_records_with_a_full_pack_gets_a_real_verdict(self):
        """M10 (docs/plan/QUEUE.json). Check 4's own comment used to close
        with "the one legitimate case, a brand new session that never
        claimed anything, is exactly the case with no pack to close
        either" -- disproven 2026-08-15 by a session that did real,
        closeable work (a full, clean, zipped pack) while claiming zero
        fence records.

        REPRODUCED pre-fix: BM_FENCE_SESSION_ID set to a session id that
        owns no record anywhere in this store (never once called
        self.claim), every other check satisfied (filled, zipped, status
        line present), and verify-close returned NO-DATA instead of PASS,
        because "session not in known" was read as an unanswerable
        question even for an id nobody could have mistyped: it came
        straight from the harness's own env var, not a human's --session
        flag."""
        os.environ["BM_FENCE_SESSION_ID"] = "never-claimed-anything"
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)
        self.assertNotIn("NO-DATA", out)

    def test_m10_explicit_session_flag_still_refuses_an_unknown_id(self):
        """The other half: the typo guard M10 must NOT weaken. A
        human-typed --session naming an id the store has never seen is
        exactly what a typo produces, and stays NO-DATA, never PASS,
        even though every other check on this same pack is clean."""
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session",
            "audit-fake-session")
        self.assertNotEqual(0, code, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_f6_adopted_state_is_also_unparked_and_owned(self):
        """A2b: with --session given, the old check filtered state=="active"
        only. An "adopted" record is equally unparked and equally owned
        by the session that adopted it, and must FAIL too."""
        rec = self.claim("fence-alpha", session_id="closing-session")
        self.store.transition(
            rec.lifecycle_uuid, rec.version, "adopted",
            session_id="closing-session", note="adopted by test")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn(rec.lifecycle_uuid[:8], out)

    def test_f2_all_six_narrative_files_zero_bytes_fails(self):
        """A1c2: truncating every narrative file to zero bytes removed the
        FILL-BY-HAND substring the old scan looked for, so it PASSed. An
        empty file must FAIL."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        for name in bh.PACK_FILES:
            if name == "06-CLOSE-REPORT.md":
                continue
            self.write(os.path.join(pack_dir, name), "")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("empty", out.lower())

    def test_f3_deleted_human_block_and_markers_fails(self):
        """A1a: deleting the whole human block, markers included, leaves
        no FILL-BY-HAND substring for the old scan to find. A file with no
        human block at all must FAIL, not be read as clean."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        target = os.path.join(pack_dir, "00-READ-ME-FIRST.md")
        text = self.read(target)
        pre, _, rest = text.partition(bs.HUMAN_BLOCK_BEGIN)
        _mid, _sep, post = rest.partition(bs.HUMAN_BLOCK_END)
        self.write(target, pre + post)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("00-READ-ME-FIRST.md", out)

    def test_f3_human_block_emptied_to_whitespace_fails(self):
        """A1b: a block emptied to whitespace only carries no FILL-BY-HAND
        substring either, so it also used to PASS."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        target = os.path.join(pack_dir, "01-HANDOVER.md")
        self.fill_human_block(target, "   \n   \n")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("01-HANDOVER.md", out)

    def test_f15_lowercase_fill_by_hand_still_fails(self):
        """A1e: a block left saying lowercase "fill-by-hand" used to PASS
        because the scan was case sensitive. The marker scan is now case
        insensitive."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        target = os.path.join(pack_dir, "03-RULES-AND-PROCESS-FIXES.md")
        self.fill_human_block(target, "fill-by-hand: not actually filled in.")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("03-RULES-AND-PROCESS-FIXES.md", out)

    def _status_line_case(self, status_line):
        """Fill and zip a pack whose ONLY defect is the close report's
        status line, and with no session ever claimed: isolates check 2
        from checks 3 (zip freshness, satisfied because the zip is made
        AFTER this exact content) and 4 (no record exists to own), so a
        FAIL can only come from the status line check itself, never from
        an unrelated confound coincidentally producing the same exit
        code (the mistake an earlier draft of this test made: the OLD
        code's check 3 FAILed for "no zip", which happened to match the
        expected exit code even though check 2's own defect was never
        exercised)."""
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir, status_line=status_line)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        return pack_dir

    def test_f10_finished_inside_code_fence_fails(self):
        """A1f: a close report whose block is a code fence wrapping the
        word FINISHED used to PASS, because the scan matched the word
        ANYWHERE in the file. It must now FAIL: the status line has to be
        the block's own first non-empty line."""
        pack_dir = self._status_line_case("```\nFINISHED\n```")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)

    def test_f10_finished_buried_mid_file_fails(self):
        """A1g: FINISHED buried on the fourth line under other prose used
        to PASS for the same reason."""
        pack_dir = self._status_line_case(
            "Some notes first.\nMore notes.\nStill more.\n"
            "FINISHED: buried, not the opening line.")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)

    def test_f10_finishedness_prefix_is_not_a_whole_word_match(self):
        """A1j: "FINISHEDNESS is a myth..." used to PASS on a prefix
        match. The match must be whole word."""
        pack_dir = self._status_line_case(
            "FINISHEDNESS is a myth and we quit early")
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)

    def test_f5_r2b_pack_files_replaced_with_symlinks_to_decoy_fails(self):
        """R2b: every pack file deleted and replaced with a symlink to one
        decoy file outside the project, reading a fake FINISHED status.
        verify-close applied no containment and PASSed."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        decoy = os.path.join(self.tmp, "decoy.md")
        self.write(decoy, "%s\n%s\nFINISHED: nothing to see here.\n%s\n"
                          % (bs.HUMAN_BLOCK_BEGIN, "", bs.HUMAN_BLOCK_END))
        for name in bh.PACK_FILES:
            target = os.path.join(pack_dir, name)
            os.remove(target)
            os.symlink(decoy, target)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertNotEqual(0, code, out)
        self.assertNotIn("PASS", out)
        self.assertIn("symlink", out.lower(),
                      "expected the containment gate's own refusal "
                      "message, naming the symlink, not some other "
                      "check FAILing to coincidentally match")

    def test_f13_zip_pack_arbitrary_absolute_directory_refused(self):
        """A6d: `zip --pack <any absolute dir>` used to package an
        unrelated directory, secrets included, into the founder's own
        handovers archive with no containment check at all."""
        outside = os.path.join(self.tmp, "fake-dot-ssh")
        os.makedirs(outside)
        self.write(os.path.join(outside, "id_rsa"), "not a real key\n")
        code, out, err = self.run_cli("zip", "--pack", outside)
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertFalse(os.path.isdir(self.handovers_dir)
                         and os.listdir(self.handovers_dir),
                         "a zip was written despite the pack being outside "
                         "the project")

    def test_f13_zip_pack_project_root_refused(self):
        """A6e: `zip --pack <project root>` used to package the whole
        project, secrets.env included, with no containment check."""
        self.write(os.path.join(self.root, "secrets.env"),
                   # DELIBERATELY NOT KEY SHAPED. The property under test is
                   # that zip refuses a project root, and the file content is
                   # irrelevant to it. A realistic sk- literal here would trip
                   # every future push secret scan and every scanner on a
                   # public repository, training people to wave alerts through.
                   "CREDENTIAL_PLACEHOLDER=not-a-real-value\n")
        code, out, err = self.run_cli("zip", "--pack", self.root)
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))

    def test_f9_same_content_resave_does_not_deadlock_the_ceremony(self):
        """R2d: zip an unchanged pack, then re save a pack file with
        BYTE-IDENTICAL content (an editor or formatter no-op save). The
        old mtime based freshness check FAILed forever with a remedy
        (re run zip) that could never clear it, because zip's own
        idempotence branch left the existing zip's mtime untouched. The
        freshness check is now content based, so this must PASS."""
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        # Re save one pack file with the exact same bytes it already had,
        # the way an editor's "save" or a formatter's no-op pass would.
        target = os.path.join(pack_dir, "01-HANDOVER.md")
        same_bytes = self.read(target)
        time.sleep(0.05)
        self.write(target, same_bytes)
        code, out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out)
        self.assertIn("unchanged", out.lower())
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def test_f9b_resave_across_a_two_second_boundary_is_still_unchanged(self):
        """F9 again, made DETERMINISTIC (2026-08-12).

        The test above re saves a file after a 0.05 second sleep and expects
        "unchanged". It passed almost always and failed inside the gate
        twice, refusing to reproduce in five isolated runs, which is what a
        narrow timing window looks like from the outside.

        The window: F9's fix compared the ARCHIVE'S BYTES rather than the
        pack directory's mtime, on the reasoning that bytes are content. A
        zip entry header stores each member's modification time, so
        zf.write() bakes the file's mtime into those bytes. A no-op re save
        moves that mtime, and once it crosses one of the MS DOS format's two
        second boundaries the archive's bytes change with the content
        untouched. A 0.05 second sleep crosses such a boundary about one
        time in forty.

        This test removes the dice: it sets the mtime forward by exactly two
        seconds with os.utime, contents byte identical, which lands the
        change in a different DOS timestamp EVERY time. Against the
        byte-comparing implementation it fails on every run. It passes only
        when the comparison ignores timestamps.
        """
        rec = self.claim("fence-f9b", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")

        target = os.path.join(pack_dir, "01-HANDOVER.md")
        same_bytes = self.read(target)
        self.write(target, same_bytes)
        # Forward by a full two seconds: a different DOS timestamp with
        # certainty, not with probability.
        moved = os.stat(target).st_mtime + 2
        os.utime(target, (moved, moved))

        code, out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out)
        self.assertIn(
            "unchanged", out.lower(),
            "an mtime-only change rewrote the archive, so the freshness "
            "check is comparing timestamps again rather than content: " + out)

        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def _write_forecast_log(self, lines):
        """Put a forecast log where bm_forecast.resolve_log_path will find
        it for this test's root."""
        d = os.path.join(self.root, ".brothermode")
        if not os.path.isdir(d):
            os.makedirs(d)
        p = os.path.join(d, "forecasts-log.jsonl")
        with io.open(p, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
        return p

    def _clean_close(self):
        """A pack that would PASS on every check except the one under test."""
        rec = self.claim("fence-fc", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _o, _e = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        return pack_dir

    def test_a3_a_forecast_left_open_past_its_window_refuses_the_close(self):
        """A3 (2026-08-12). The forecasting bias that scored this project a
        3 out of 10 survived four estimates in one night for one reason:
        nothing ever compared a forecast against what happened, because
        nothing ever REQUIRED it. tools/bm_forecast.py records both, and a
        tool that is only run when somebody remembers is a rule rather than
        a control. This is the line that makes it a control."""
        pack_dir = self._clean_close()
        self._write_forecast_log([
            {"kind": "forecast", "task": "never-closed", "clock": "agent",
             "basis": "judged", "likely_minutes": 30.0,
             "recorded_at": "2020-01-01T00:00:00Z"},
        ])
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(1, code, out)
        self.assertIn("never-closed", out)
        self.assertIn("never got an actual", out)

    def test_a3_a_forecast_that_got_its_actual_does_not_block_the_close(self):
        """The same log with the actual recorded must PASS. A gate that
        cannot be satisfied is not a gate, it is a wall."""
        pack_dir = self._clean_close()
        self._write_forecast_log([
            {"kind": "forecast", "task": "properly-closed", "clock": "agent",
             "basis": "judged", "likely_minutes": 30.0,
             "recorded_at": "2020-01-01T00:00:00Z"},
            {"kind": "actual", "task": "properly-closed", "minutes": 8.0,
             "recorded_at": "2020-01-01T00:30:00Z"},
        ])
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def test_a3_a_project_with_no_forecast_log_is_not_punished(self):
        """The conservative half, and the one worth arguing about. A
        repository that has never used the forecasting tool has no log at
        all. Failing it would block every project this ceremony is meant to
        serve, in order to enforce a tool they never adopted. So an absent
        log passes, and bm_forecast.py's own check verb is where a project
        that cares gates on could-not-tell."""
        pack_dir = self._clean_close()
        log = os.path.join(self.root, ".brothermode", "forecasts-log.jsonl")
        self.assertFalse(os.path.exists(log))
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertIn("PASS", out)

    def _fake_git(self, commit_mtime, head="ref: refs/heads/main"):
        """A minimal .git that cmd_owed can read: HEAD naming a ref, and the
        ref file whose mtime stands in for the last commit."""
        gd = os.path.join(self.root, ".git", "refs", "heads")
        if not os.path.isdir(gd):
            os.makedirs(gd)
        self.write(os.path.join(self.root, ".git", "HEAD"), head + "\n")
        ref = os.path.join(gd, "main")
        self.write(ref, "0" * 40 + "\n")
        os.utime(ref, (commit_mtime, commit_mtime))
        return ref

    def test_verify_close_refuses_a_session_id_the_store_never_saw(self):
        """An independent audit defeated the ownership check with
        `--session audit-fake-session`. A session id the store has never seen
        owns no records, therefore owns no UNPARKED records, therefore passed.
        The check the founder relies on was defeated by a typo. Unanswerable
        is not clean."""
        rec = self.claim("fence-typo", session_id="real-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _o, _e = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "not-a-real-session")
        self.assertEqual(
            2, code,
            "an unknown session id must be NO-DATA, never a pass: " + out)
        self.assertTrue(out.startswith("NO-DATA:"), out)
        # And the real one still behaves: it owns an unparked record, so FAIL.
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "real-session")
        self.assertEqual(1, code, out)
        self.assertIn("unparked", out)
        self.park(rec, session_id="real-session")

    def test_owed_fires_when_the_newest_pack_is_not_committed(self):
        """The founder's failure verbatim: a pack written, zipped, and never
        committed dies with the checkout. The first design of this verb
        compared mtimes and an audit broke it four ways in an hour, including
        reading OWED forever after a CORRECT close, because this project
        commits last. Committedness has no such false positive."""
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        self._real_git_repo()            # real repo, nothing staged
        code, out, _err = self.run_cli("owed")
        self.assertEqual(1, code, out)
        self.assertIn("OWED", out)
        self.assertIn("git does not have", out)

    def test_owed_is_current_once_the_pack_is_staged(self):
        """A gate that cannot be satisfied is a wall. Once the pack is in
        git's index it is safe from losing the checkout, which is the whole
        question this verb asks."""
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        rel = os.path.relpath(pack_dir, self.root)
        self._real_git_repo(add_paths=(rel,))
        code, out, _err = self.run_cli("owed")
        self.assertEqual(0, code, out)
        self.assertIn("CURRENT", out)

    def test_owed_is_no_data_outside_a_git_repository_never_a_pass(self):
        """A project not under git cannot be asked whether its files are
        committed. NO-DATA is the honest answer; reporting CURRENT would be
        a check that reassures precisely where it cannot see."""
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, out, _err = self.run_cli("owed")
        self.assertEqual(2, code, out)
        self.assertTrue(out.startswith("NO-DATA:"), out)

    def test_verify_close_refuses_a_pack_git_does_not_have(self):
        """The founder's own failure mode, found by an independent review
        rather than by this file's author.

        A session can write a pack, zip it, and collect PASS while the pack
        sits untracked on disk. If that checkout is a worktree somebody
        cleans up, or the machine is rebuilt, the handover the ceremony just
        certified is gone. A pack nobody committed is a local file, not a
        handover."""
        rec = self.claim("fence-git", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _o, _e = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        self._real_git_repo()         # a real repo with nothing staged
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(1, code, out)
        self.assertIn("not in git", out)

    def test_verify_close_is_no_data_when_the_index_cannot_be_read(self):
        """Could-not-tell is never a pass. An index this cannot parse means
        the question is unanswered, and reporting nothing-untracked would be
        the fail-open shape this project already has a finding about."""
        rec = self.claim("fence-git2", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _o, _e = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")
        self._real_git_repo()
        gd = os.path.join(self.root, ".git")
        self.write(os.path.join(gd, "index"), "this is not a git index")
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(2, code, out)
        self.assertTrue(out.startswith("NO-DATA:"), out)

    def test_f9b_a_real_content_change_is_still_caught(self):
        """The other half, and the one that matters more. Making the
        freshness check ignore timestamps must not make it ignore CHANGES.
        A pack file whose bytes actually differ has to be caught, or the
        fix above would have turned a flaky check into a useless one."""
        rec = self.claim("fence-f9c", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        self.park(rec, session_id="closing-session")

        target = os.path.join(pack_dir, "01-HANDOVER.md")
        self.write(target, self.read(target) + "\none genuinely new line\n")

        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(
            1, code,
            "a real content change slipped past the freshness check: " + out)
        self.assertIn("does not match", out)

    def test_f7_pack_subdirectory_refused_not_silently_dropped(self):
        """A2f: a subdirectory inside the pack (e.g. evidence/) used to be
        silently excluded from the zip, with no word said, and its edits
        never moved the pack's tracked freshness either."""
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)
        evidence_dir = os.path.join(pack_dir, "evidence")
        os.makedirs(evidence_dir)
        self.write(os.path.join(evidence_dir, "gate-receipt.json"), "{}")
        code, out, err = self.run_cli("zip", "--pack", pack_dir)
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertNotEqual(0, code)
        self.assertNotIn("PASS", out)

    def test_f11_command_center_copy_is_checked_by_verify_close(self):
        """R2a: the pack is missing the command center copy the project
        has at generation time (deleted after skeleton ran); verify-close
        used to iterate only the seven PACK_FILES and never notice."""
        cc_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(cc_dir)
        self.write(os.path.join(cc_dir, "COMMAND-CENTER.html"),
                   "<html>command center</html>")
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        cc_in_pack = os.path.join(pack_dir, "COMMAND-CENTER.html")
        self.assertTrue(os.path.isfile(cc_in_pack),
                        "the command center copy was not included even "
                        "though the project has one at generation time")
        self.fill_pack(pack_dir)
        os.remove(cc_in_pack)
        code, out, _err = self.run_cli("verify-close", "--pack", pack_dir)
        self.assertEqual(1, code, out)
        self.assertIn("FAIL", out)
        self.assertIn("COMMAND-CENTER.html", out)

    def test_f11_command_center_page_and_stdout_agree_on_first_run(self):
        """Secondary F11 bug, found by hand: 00-READ-ME-FIRST.md used to
        say "no copy is included" (it checked the pack's DESTINATION,
        which does not have the file yet on a first run) while stdout said
        "command center copy: included" and the copy WAS written moments
        later. Both must agree, because they now share one source of
        truth (whether the project's own command center exists)."""
        cc_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(cc_dir)
        self.write(os.path.join(cc_dir, "COMMAND-CENTER.html"),
                   "<html>command center</html>")
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("board copy: included (COMMAND-CENTER.html)", out)
        read_me = self.read(os.path.join(out_dir, "00-READ-ME-FIRST.md"))
        self.assertNotIn("no copy is included", read_me,
                         "00-READ-ME-FIRST.md contradicts stdout: it says "
                         "no copy is included while stdout says included "
                         "and the file IS in the pack")
        self.assertIn("is included as", read_me)

    def test_f4_decision_and_handover_text_withheld_from_page_and_stdout(self):
        """A5: decisions.text, decisions.topic and handovers.heading are
        withheld by bs.export_column's own central policy, yet used to be
        written verbatim to a committed pack page and to detect's stdout,
        exactly the class of leak already fixed once for session ids."""
        founder_text = "whether to tell investors about the runway"
        founder_heading = "Baton: the deal is unsigned, we are out of cash"
        rec = self.claim("fence-alpha", session_id="closing-session")
        self.store.decide(rec.lifecycle_uuid, rec.version, founder_text,
                          "Khalil decided to drop the account.")
        self.store.transition(
            rec.lifecycle_uuid, rec.version + 1, "parked",
            session_id="closing-session", note="parked by test",
            handover_heading=founder_heading)
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        page = self.read(os.path.join(out_dir, "01-HANDOVER.md"))
        self.assertNotIn(founder_text, page)
        self.assertNotIn(founder_heading, page)
        code, detect_out, _err = self.run_cli("detect")
        self.assertEqual(0, code)
        self.assertNotIn(founder_heading, detect_out)

    def test_f12_detect_survives_an_unreadable_pack_directory(self):
        """A4c: detect used to exit 1 with an uncaught PermissionError
        traceback when a pack directory could not be listed. Section 4:
        detect is read only, exit 0 always."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root: permission bits are not "
                          "enforced, so this fixture cannot reproduce "
                          "the defect")
        self.claim("fence-alpha")
        pack_dir = self._fresh_pack()
        os.chmod(pack_dir, 0o000)
        try:
            code, out, err = self.run_cli("detect")
        finally:
            os.chmod(pack_dir, 0o700)
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))

    def test_f14_absolute_paths_masked_in_verify_close_stdout(self):
        """A5, secondary: verify-close's own stdout used to carry the
        founder's real absolute filesystem layout unmasked, even though
        the ceremony asks a session to quote this line into a session log
        or a committed pack page."""
        rec = self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        self.run_cli("zip", "--pack", pack_dir)
        self.park(rec, session_id="closing-session")
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertEqual(0, code, out)
        self.assertNotIn(self.tmp, out)
        self.assertIn(bs.PATH_WITHHELD_MARKER, out)

    def test_f16_absolute_out_is_refused_not_silently_reinterpreted(self):
        """A6b: `skeleton --out /tmp/abs-out-evil` used to be silently
        reinterpreted as relative to the project root instead of refused,
        so the caller's typo landed somewhere they never named."""
        self.claim("fence-alpha")
        code, out, err = self.run_cli(
            "skeleton", "--out", "/tmp/abs-out-evil", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertFalse(
            os.path.isdir(os.path.join(self.root, "tmp", "abs-out-evil")),
            "the absolute --out was silently reinterpreted as relative")
        self.assertFalse(os.path.isdir("/tmp/abs-out-evil"))


# ---------------------------------------------------------------------------
# CHANGE 3: the pack copies the LIVE board (GANTT.html preferred over the
# older COMMAND-CENTER.html), not whichever page happens to be stale.
# ---------------------------------------------------------------------------

class TestBoardCopy(HandoverCase):
    """Pre-fix: cmd_skeleton only ever looked at docs/plan/COMMAND-
    CENTER.html. This project's own live board moved to docs/plan/
    GANTT.html on 2026-08-17 to 2026-08-20 while COMMAND-CENTER.html sat
    unwritten, so every pack in that window copied a stale board under a
    name nobody was looking at."""

    def test_gantt_preferred_and_named_with_its_own_date(self):
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        self.write(os.path.join(plan_dir, "COMMAND-CENTER.html"),
                   "<html>stale board</html>")
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>live board</html>")
        mtime = time.time() - 3600
        os.utime(gantt, (mtime, mtime))
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertTrue(
            os.path.isfile(os.path.join(out_dir, "GANTT.html")),
            "GANTT.html was not copied even though it exists")
        self.assertFalse(
            os.path.isfile(os.path.join(out_dir, "COMMAND-CENTER.html")),
            "the stale COMMAND-CENTER.html was copied even though the "
            "live GANTT.html exists")
        self.assertIn("board copy: included (GANTT.html)", out)
        read_me = self.read(os.path.join(out_dir, "00-READ-ME-FIRST.md"))
        self.assertIn("GANTT.html", read_me)
        stamp = time.strftime("%Y-%m-%d", time.localtime(mtime))
        self.assertIn(stamp, read_me,
                      "the board's own modification date is not named")

    def test_command_center_fallback_when_gantt_absent(self):
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        self.write(os.path.join(plan_dir, "COMMAND-CENTER.html"),
                   "<html>only board</html>")
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertTrue(
            os.path.isfile(os.path.join(out_dir, "COMMAND-CENTER.html")),
            "the fallback board was not copied when GANTT.html is absent")
        self.assertIn("board copy: included (COMMAND-CENTER.html)", out)

    def test_no_board_at_all_is_named_rather_than_shipped_quietly(self):
        self.claim("fence-alpha")
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("no docs/plan/GANTT.html or docs/plan/"
                      "COMMAND-CENTER.html found, skipped", out)
        read_me = self.read(os.path.join(out_dir, "00-READ-ME-FIRST.md"))
        self.assertIn("No board was found", read_me)


# ---------------------------------------------------------------------------
# V4: zip
# ---------------------------------------------------------------------------

class TestZip(HandoverCase):

    def test_v4_zip_creates_dated_zip_and_is_idempotent(self):
        self.claim("fence-alpha")
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-11", "--slot", "zipme")
        self.assertEqual(0, code)
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-11-zipme")
        code, out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out)
        zip_path = os.path.join(
            self.handovers_dir,
            "BrotherMode-Handover-2026-08-11-zipme.zip")
        self.assertTrue(os.path.isfile(zip_path), out)
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        for pf in bh.PACK_FILES:
            self.assertTrue(
                any(n.endswith(pf) for n in names),
                "%s not found in zip entries %r" % (pf, names))
        first_mtime = os.path.getmtime(zip_path)
        time.sleep(0.05)
        code, out2, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out2)
        self.assertIn("unchanged", out2.lower())
        self.assertEqual(first_mtime, os.path.getmtime(zip_path),
                         "an unchanged rerun rewrote the zip file")

    def test_m19_zip_refreshes_a_board_copy_gone_stale_since_skeleton(self):
        """M19: the pack's board copy is captured once, at skeleton time,
        and used to sit there unrefreshed. A session that folds a finding
        into the live board (docs/plan/GANTT.html) AFTER generating the
        pack, then runs zip, used to archive that stale copy with nothing
        to say so: cmd_zip only ever read whatever file was already
        sitting in pack_dir, it never compared it against the live board.
        zip must pick up whatever the live board says right now, not
        whatever it said when skeleton ran."""
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>board before the finding landed</html>")
        self.claim("fence-alpha")
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-20", "--slot", "boardzip")
        self.assertEqual(0, code)
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-20-boardzip")
        board_in_pack = os.path.join(pack_dir, "GANTT.html")
        self.assertEqual("<html>board before the finding landed</html>",
                         self.read(board_in_pack))

        # The live board moves on AFTER the pack was generated: the exact
        # sequence the queue item describes, a finding folded into the
        # board, then zip re-run over the same pack.
        self.write(gantt, "<html>board after the finding landed</html>")

        code, out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out)

        self.assertEqual(
            "<html>board after the finding landed</html>",
            self.read(board_in_pack),
            "zip left the pack's own board copy on disk stale even "
            "though it just archived it")

        zip_path = os.path.join(
            self.handovers_dir,
            "BrotherMode-Handover-2026-08-20-boardzip.zip")
        with zipfile.ZipFile(zip_path) as zf:
            archived = zf.read(
                "2026-08-20-boardzip/GANTT.html").decode("utf-8")
        self.assertEqual(
            "<html>board after the finding landed</html>", archived,
            "the zip archived a board copy that does not match the live "
            "board on disk")

    def test_m19_defect_one_zip_refuses_a_destination_symlink_before_write(
            self):
        """Outside review of the M19 fix above, reproduced by executed
        command: pack_dir/GANTT.html was made a symlink pointing at a file
        OUTSIDE the project. cmd_zip's re copy of the live board followed
        it: shutil.copy2 opens its destination for writing and does not
        care that the name is a symlink, so the external file's content
        was already overwritten with the live board's HTML by the time the
        containment gate in _pack_files_for_zip got a turn to refuse it.

        Asserting only that an exception was raised would have PASSED
        against the broken code too, since the broken code also raised,
        just too late. The assertion that actually pins the fix is the
        external file's content, unchanged."""
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>the live board</html>")
        self.claim("fence-alpha")
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-20", "--slot", "symlinkzip")
        self.assertEqual(0, code)
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-20-symlinkzip")
        board_in_pack = os.path.join(pack_dir, "GANTT.html")

        outside = os.path.join(self.tmp, "external-board.html")
        sentinel = "EXTERNAL FILE BEFORE ANYTHING RAN. DO NOT TOUCH."
        self.write(outside, sentinel)
        os.remove(board_in_pack)
        os.symlink(outside, board_in_pack)

        code, out, err = self.run_cli("zip", "--pack", pack_dir)
        self.assertNotEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertIn("symlink", (out + err).lower(),
                      "expected the containment refusal's own message, "
                      "naming the symlink, not some other failure")

        self.assertEqual(
            sentinel, self.read(outside),
            "cmd_zip wrote through the destination symlink before "
            "refusing: the external file was overwritten with the live "
            "board's HTML")

    def test_m19_defect_two_a_committed_pack_going_stale_is_catchable(self):
        """Outside review of the M19 fix above, reproduced by executed
        command: skeleton wrote board A into the pack, the pack was
        committed, the live board was edited to B, zip ran again.
        Afterwards `git diff --stat HEAD` showed the pack's GANTT.html
        modified, `git show HEAD:<pack>/GANTT.html` still read board A,
        the working copy read board B, and verify-close returned PASS
        anyway: the durable committed record held the stale board while
        every check reported healthy.

        zip must say plainly that it just made the pack dirty, and
        verify-close must refuse to certify a pack whose tracked files no
        longer match what git staged for them."""
        plan_dir = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan_dir)
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>BOARD A</html>")
        rec = self.claim("fence-alpha", session_id="closing-session")
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-20", "--slot", "dirtyzip")
        self.assertEqual(0, code)
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-20-dirtyzip")
        self.fill_pack(pack_dir)
        code, _out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code)

        rel = os.path.relpath(pack_dir, self.root)
        git = self._real_git_repo(add_paths=(rel,))
        git("commit", "-q", "-m", "pack committed with board A")

        # The live board moves on AFTER the pack was committed: the exact
        # sequence the outside review describes.
        self.write(gantt, "<html>BOARD B</html>")
        code, out, _err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, out)
        self.assertIn(
            "dirty", out.lower(),
            "zip refreshed a committed pack's board copy and said "
            "nothing about it: " + out)

        self.park(rec, session_id="closing-session")
        code, out, _err = self.run_cli(
            "verify-close", "--pack", pack_dir, "--session", "closing-session")
        self.assertNotEqual(
            0, code,
            "verify-close PASSed on a pack whose committed record still "
            "reads BOARD A while the working tree reads BOARD B: " + out)
        self.assertIn("FAIL", out)
        self.assertIn("GANTT.html", out)


# ---------------------------------------------------------------------------
# V5: detect
# ---------------------------------------------------------------------------

class TestDetect(HandoverCase):

    def test_v5a_empty_estate_is_stated_no_data_not_silence(self):
        code, out, _err = self.run_cli("detect")
        self.assertEqual(0, code)
        self.assertTrue(out.strip(), "detect printed nothing at all")
        self.assertIn("NO-DATA", out)

    def test_v5b_reports_dead_session_leftover_with_clearing_command(self):
        real_now_iso = bs.now_iso
        ten_hours_ago = (
            __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc)
            - __import__("datetime").timedelta(hours=10))
        bs.now_iso = lambda: ten_hours_ago.strftime(bs._ISO_STAMP_FORMAT)
        try:
            rec = self.claim("stale-fence", session_id="dead-session")
        finally:
            bs.now_iso = real_now_iso
        code, out, _err = self.run_cli("detect")
        self.assertEqual(0, code, out)
        self.assertIn(rec.lifecycle_uuid[:8], out)
        self.assertIn("adopt", out)


# ---------------------------------------------------------------------------
# Privacy: raw store text may drive a comparison, never a written page
# ---------------------------------------------------------------------------

class TestRawStoreTextNeverReachesAPage(HandoverCase):
    """Found by the orchestrator's own spot-check of the landed delta, and
    fixed RED first before that delta was committed.

    This file reads the store with dump(raw=True) on purpose: the default
    redacted dump WITHHOLDS a session id that does not match the codebase's
    own generated-id shapes, and verify-close's ownership test has to
    compare against the real value or it silently matches nothing. Taking
    the raw value is therefore right; WRITING it is not. A pack page is
    committed to git and zipped into a handover, so a hand-typed session id
    (which the store classifies as founder text precisely because it could
    be anything, an absolute path included) must be scrubbed on the way out,
    exactly as every other field from the same raw dump already is."""

    #: CALIBRATED, not guessed. The first version of this fixture embedded an
    #: absolute path, and both tests passed while the leak was still open: the
    #: write funnel's own path masking scrubbed it for an unrelated reason. A
    #: plain hand-typed value reproduces the real defect, and the store's own
    #: default dump renders exactly this value as founder text it withholds.
    HAND_TYPED = "khalil-private-note-do-not-publish"

    def test_a_hand_typed_session_id_is_scrubbed_out_of_the_pack_page(self):
        self.claim("fence-alpha", session_id=self.HAND_TYPED)
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        page = self.read(os.path.join(out_dir, "01-HANDOVER.md"))
        self.assertNotIn(
            self.HAND_TYPED, page,
            "the raw session id reached a generated pack page unscrubbed; "
            "pack pages are committed and zipped, so founder text out of "
            "the store must pass through _scrub on the way out")

    def test_the_ownership_check_still_matches_that_same_raw_value(self):
        """The other half of the pair: scrubbing for display must not break
        the comparison the raw read exists for. A session that still owns an
        active record FAILs verify-close, scrubbed page or not."""
        self.claim("fence-alpha", session_id=self.HAND_TYPED)
        self.run_cli("skeleton", "--out", "docs/handover/pack1", "--date",
                     "2026-08-11", "--slot", "pack1")
        pack_rel = "docs/handover/pack1"
        self.fill_pack(os.path.join(self.root, pack_rel))
        code, out, err = self.run_cli(
            "verify-close", "--pack", pack_rel, "--session", self.HAND_TYPED)
        self.assertNotEqual(
            0, code,
            "verify-close passed while that session still owned an active "
            "record: stdout=%r stderr=%r" % (out, err))


class TestAForgedHumanMarkerInAClaimedPathCannotDisableRedaction(HandoverCase):
    """SBE14, held at this generator (tools/plan/PROBLEMS-2026-08-16.md:
    "redaction can be switched off by the text it redacts... the guard
    every other protection leans on").

    tools/test_bm_store.py's TestAForgedHumanMarkerCannotDisableRedaction
    already proves the funnel itself redacts an unterminated marker. That
    is not what broke here: bm_docs.py and bm_packs.py route every store
    field through _d (bm_learning.safe_display), which strips an embedded
    newline before a field can start a line of its own
    (test_a_store_field_containing_the_marker_cannot_forge_a_human_block,
    tools/test_bm_docs.py). This generator's own _scrub does not strip
    newlines, and claims.path is a scrub-only column, not a withheld one
    (bm_store.py's _DUMP_SCRUB_ONLY_COLUMNS), so a claimed path may
    legally hold one. A path whose second line equals the human-block
    begin marker therefore reached render_page's `header` as a real,
    physical line: a REAL human block, closed by the genuine one this
    page's own NOTES section always ends in, opened right there, and
    everything between (a live fence claim, both section headings below
    it) round-tripped through the funnel untouched instead of being
    redacted, exactly what the funnel exists to prevent."""

    def test_a_marker_line_in_a_claimed_path_is_neutralized_not_honored(self):
        evil_path = "evil.py\n%s\nunredacted generated content" % bh.HUMAN_BEGIN
        self.claim("fence-alpha", files=[evil_path])
        out_dir = os.path.join(self.root, "docs", "handover", "pack1")
        code, out, err = self.run_cli(
            "skeleton", "--out", "docs/handover/pack1", "--date",
            "2026-08-11", "--slot", "pack1")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        page = self.read(os.path.join(out_dir, "01-HANDOVER.md"))
        self.assertNotIn(
            "evil.py\n%s" % bh.HUMAN_BEGIN, page,
            "a marker line smuggled in through a claimed path survived "
            "verbatim: it opened a fake human block that the page's own "
            "real closing marker then terminated, exempting everything "
            "between from redaction")
        # A fresh parse of the WRITTEN file must find only the one real
        # block (the page's own NOTES section), never a forged second one.
        blocks = bh._pack_file_blocks(page)
        self.assertEqual(
            len(blocks), 1,
            "the forged marker still parses as opening a second human "
            "block: %r" % (blocks,))
        self.assertIn("FILL-BY-HAND", blocks[0])


class TestZipBoardIdentityChanged(HandoverCase):
    """M21. The M19 fix taught zip to REFRESH the pack's board copy from the
    live board, which closed the stale-content hole. It left two holes open,
    both about the board's IDENTITY rather than its content, and in both the
    pack ends up describing a board that is not the one on disk.

    APPEARED: skeleton runs when only COMMAND-CENTER.html exists, so the pack
    carries a copy under that name and 00-READ-ME-FIRST.md points at it. A
    GANTT.html is then written, which BOARD_CANDIDATES prefers. zip refreshes
    GANTT.html into the pack and never looks at the COMMAND-CENTER.html copy
    already sitting there, so the pack ships BOTH boards while its own read-me
    still names the stale one.

    DISAPPEARED: the live board is removed after skeleton. `_board_source`
    returns (None, None), the whole refresh block is skipped by `if board_abs:`
    and the pack's copy is archived unchanged, with nothing printed to say the
    archived board no longer corresponds to anything in the project.

    Both directions are silent today, which is what makes them worth a test:
    the failure is not a wrong verdict, it is the absence of one."""

    def _plan_dir(self):
        d = os.path.join(self.root, "docs", "plan")
        if not os.path.isdir(d):
            os.makedirs(d)
        return d

    def test_zip_reports_a_board_that_appeared_under_another_name(self):
        plan_dir = self._plan_dir()
        self.write(os.path.join(plan_dir, "COMMAND-CENTER.html"),
                   "<html>the board skeleton copied</html>")
        self.claim("fence-alpha")
        code, out, err = self.run_cli(
            "skeleton", "--date", "2026-08-11", "--slot", "appeared")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-11-appeared")
        self.assertTrue(
            os.path.isfile(os.path.join(pack_dir, "COMMAND-CENTER.html")))

        # The live board now moves to the preferred candidate, exactly the
        # move this project itself made on 2026-08-17.
        self.write(os.path.join(plan_dir, "GANTT.html"),
                   "<html>the board that is actually live</html>")

        code, out, err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))

        # Pre-fix both copies sit in the pack and nothing says so.
        self.assertTrue(os.path.isfile(os.path.join(pack_dir, "GANTT.html")),
                        "the live board was not refreshed into the pack")
        self.assertIn(
            "COMMAND-CENTER.html", out,
            "zip did not name the superseded board copy still in the pack; "
            "the pack carries two boards and its read-me points at the "
            "stale one")
        self.assertIn("superseded", out)

    def test_zip_reports_a_board_that_vanished(self):
        plan_dir = self._plan_dir()
        gantt = os.path.join(plan_dir, "GANTT.html")
        self.write(gantt, "<html>live board</html>")
        self.claim("fence-alpha")
        code, out, err = self.run_cli(
            "skeleton", "--date", "2026-08-11", "--slot", "vanished")
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        pack_dir = os.path.join(self.root, "docs", "handover",
                                "2026-08-11-vanished")
        self.assertTrue(os.path.isfile(os.path.join(pack_dir, "GANTT.html")))

        os.remove(gantt)

        code, out, err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(0, code, "stdout=%r stderr=%r" % (out, err))
        self.assertIn(
            "GANTT.html", out,
            "zip archived a board copy whose live source is gone and said "
            "nothing; board_abs is None so the whole refresh block is "
            "skipped")
        self.assertIn("no longer exists", out)


class TestZipRefusesASecretInAHandFilledSlot(HandoverCase):
    """SBE16. cbf5c6e implemented _pack_secret_hits and wired it into
    cmd_zip, and shipped no test for it. The row's own done_check asks that
    the refusal be "proven failing first"; nothing proved it at all, so the
    only thing standing between a hand-typed credential and a cloud synced
    folder was code nobody exercised.

    Why this path and not the commit-time secret scan: the ceremony has a
    human fill every FILL-BY-HAND block BY HAND, after the redaction funnel
    has already run over the generated parts, and the archive lands OUTSIDE
    the repository. So the pack's human blocks are the one place secret
    shaped text can reach a shareable artifact without passing a single
    scan.

    The planted line is deliberately NOT key shaped, for the reason the
    F13 test above already records: a realistic literal would trip every
    future push scan and every scanner on a public repository, training
    people to wave alerts through. `password:` is enough, because the
    detector reports any line redact_text would change, which is the
    property actually under test."""

    #: Assembled rather than written whole so no scanner, here or
    #: downstream, ever sees a credential-shaped literal in this file.
    TRIGGER = "the deploy " + "password" + ": ask the platform team"

    def _fresh_pack(self, slot="secretslot"):
        out_dir = os.path.join(self.root, "docs", "handover",
                               "2026-08-11-%s" % slot)
        code, _out, _err = self.run_cli(
            "skeleton", "--date", "2026-08-11", "--slot", slot)
        self.assertEqual(0, code)
        return out_dir

    def test_a_secret_shaped_line_in_a_human_block_refuses_the_zip(self):
        self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        target = os.path.join(pack_dir, "06-CLOSE-REPORT.md")
        self.fill_human_block(
            target, "FINISHED: all clear.\n" + self.TRIGGER)

        code, out, err = self.run_cli("zip", "--pack", pack_dir)

        self.assertNotEqual(
            0, code,
            "zip packaged a pack whose hand-filled block carries a secret "
            "shaped line; stdout=%r stderr=%r" % (out, err))
        both = out + err
        self.assertIn("06-CLOSE-REPORT.md", both,
                      "the refusal did not name the offending FILE")
        self.assertIn("line", both,
                      "the refusal did not name the offending LINE")
        self.assertFalse(
            os.path.isdir(self.handovers_dir)
            and [f for f in os.listdir(self.handovers_dir)
                 if f.endswith(".zip")],
            "a zip was written despite the refusal")

    def test_the_same_line_outside_a_human_block_does_not_refuse(self):
        """The negative half, and the one that stops this becoming a
        blanket grep. Generated regions go through the redaction funnel
        already, so a hit there is the funnel's job, not the zip gate's.
        Without this, a gate that refused on any occurrence anywhere would
        pass the test above while being unusable in practice."""
        self.claim("fence-alpha", session_id="closing-session")
        pack_dir = self._fresh_pack()
        self.fill_pack(pack_dir)
        target = os.path.join(pack_dir, "06-CLOSE-REPORT.md")
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n" + self.TRIGGER + "\n")

        code, out, err = self.run_cli("zip", "--pack", pack_dir)
        self.assertEqual(
            0, code,
            "zip refused a secret shaped line OUTSIDE every human block, "
            "which the redaction funnel already owns; stdout=%r stderr=%r"
            % (out, err))


if __name__ == "__main__":
    unittest.main()
