#!/usr/bin/env python3
"""Tests for the per host truth state (HG1, spec docs/plan/specs/HG1.md): host_live_verify.host_state and
truth_states map the verifier's own verdicts to certified, advisory or NO-DATA; host_capability.render_truth_table
prints them; host_live_verify.py --truth adds the table without changing the verify exit code.

The fixtures are the Fixture class of scripts/test_host_live_verify.py (a candidate checkout written as git objects by
hand, three host installs, one complete signed in run per host). Every case has its own temp folder and HOME; nothing
starts a process and nothing is written under the real home.

Run: python3 -B scripts/test_host_evidence_binding.py
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_capability  # noqa: E402
import host_live_verify as verifier  # noqa: E402
import test_host_live_verify as base  # noqa: E402  the one Fixture, never a second one

STATES = ("certified", "advisory", "NO-DATA")


class _Case(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hg1-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        for patch in (mock.patch.dict(os.environ, {"HOME": home}),
                      mock.patch.dict(sys.modules, {"host_live_claude": base.stand_in_claude()})):
            patch.start()
            self.addCleanup(patch.stop)
        self.now = int(time.time())
        self.fx = base.Fixture(self.tmp, self.now)

    def flip_to_deny(self):
        """The guard a later release ships: the table set to deny for Claude Code and Codex, the fixture rebuilt."""
        patch = mock.patch.dict(verifier.EXPECTED_GUARDED_WRITE, {"claude": "deny", "codex": "deny"})
        patch.start()
        self.addCleanup(patch.stop)
        shutil.rmtree(self.tmp)
        os.makedirs(os.path.join(self.tmp, "home"))
        self.fx = base.Fixture(self.tmp, self.now)

    def state(self, host, write=True):
        if write:
            self.fx.write_evidence()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = verifier.host_state(self.fx.evidence, self.fx.root, self.now, host)
        self.assertEqual(out.getvalue(), "")   # a state is returned, never printed
        self.assertIn(got[0], STATES, got)
        self.assertTrue(isinstance(got[1], str) and got[1], got)
        return got

    def states(self, path=None, root=None):
        return verifier.truth_states(path or self.fx.evidence, root or self.fx.root, self.now)

    def entry(self, host, probe):
        return self.fx.find(host, probe)


class TestHostState(_Case):

    # REQ-HG1-01 --------------------------------------------------------------------------------------------------------

    def test_absent_evidence_is_no_data_and_cursor_advisory(self):
        absent = os.path.join(self.tmp, "evidence", "not-recorded.jsonl")
        got = self.states(absent)
        self.assertEqual(list(got), ["claude", "codex", "antigravity", "cursor"])
        for host in ("claude", "codex", "antigravity"):
            self.assertEqual(got[host][0], "NO-DATA", got[host])
            self.assertIn("not recorded yet", got[host][1])
        self.assertEqual(got["cursor"][0], "advisory")
        self.assertIn("advisory", got["cursor"][1])
        self.assertEqual(verifier.host_state(absent, self.fx.root, self.now, "claude")[0], "NO-DATA")

    def test_absent_evidence_on_this_checkout(self):
        absent = os.path.join(self.tmp, "absent", "HP1-host-live.jsonl")
        got = verifier.truth_states(absent, os.path.dirname(HERE), self.now)
        self.assertEqual([got[h][0] for h in got], ["NO-DATA", "NO-DATA", "NO-DATA", "advisory"])

    def test_empty_evidence_is_no_data(self):
        base.write(self.fx.evidence, b"\n")
        got = self.states()
        self.assertEqual([got[h][0] for h in got], ["NO-DATA", "NO-DATA", "NO-DATA", "advisory"])

    # REQ-HG1-02 --------------------------------------------------------------------------------------------------------

    def test_shipped_unguarded_run_is_advisory_never_certified(self):
        for host in ("claude", "codex"):
            self.assertEqual(verifier.EXPECTED_GUARDED_WRITE[host], "unguarded")
            state, reason = self.state(host)
            self.assertEqual(state, "advisory", reason)
            self.assertIn("no denial is on record", reason)
            self.assertIn("GREEN", reason)

    # REQ-HG1-03 --------------------------------------------------------------------------------------------------------

    def test_deny_on_record_under_a_deny_table_is_certified(self):
        self.flip_to_deny()
        for host in ("claude", "codex"):
            state, reason = self.state(host)
            self.assertEqual(state, "certified", reason)
            self.assertIn("denial", reason)
        got = self.states()
        self.assertEqual([got[h][0] for h in got], ["certified", "certified", "NO-DATA", "advisory"])

    # REQ-HG1-04 --------------------------------------------------------------------------------------------------------

    def test_deny_with_empty_stderr_is_not_certified(self):
        self.flip_to_deny()
        self.entry("claude", "guarded_write")["row"]["stderr_len"] = 0   # exit 2, nothing said: a crash
        state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("RED", reason)

    def test_deny_with_target_written_is_not_certified(self):
        self.flip_to_deny()
        self.entry("codex", "guarded_write")["row"]["effect"]["exists_after"] = True   # the host wrote it anyway
        state, reason = self.state("codex")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("RED", reason)

    def test_certification_rechecks_the_effect_itself(self):
        """Isolates host_state's own effect check: the verifier's origin check is stood down, so only host_state
        stands between a deny whose target was written afterwards and the word certified."""
        self.flip_to_deny()
        self.entry("claude", "guarded_write")["row"]["effect"]["exists_after"] = True
        with mock.patch.object(verifier, "_origin", lambda row: ""):
            state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("effect", reason)

    # REQ-HG1-05 --------------------------------------------------------------------------------------------------------

    def test_forged_raw_byte_is_no_data_red(self):
        self.fx.write_evidence()
        row = next(r for r in verifier.load_rows(self.fx.evidence)[0]
                   if r["host"] == "claude" and r["probe"] == "verb")
        raw = os.path.join(self.fx.evidence_dir, row["raw_out_path"])
        with open(raw, "ab") as fh:
            fh.write(b"x")   # one byte changed after collection
        state, reason = self.state("claude", write=False)
        self.assertEqual(state, "NO-DATA", reason)
        self.assertTrue(reason.startswith("RED"), reason)

    def test_relabelled_probe_is_no_data_red(self):
        self.entry("codex", "door")["row"]["probe"] = "verb"
        state, reason = self.state("codex")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertTrue(reason.startswith("RED"), reason)
        self.assertIn("relabelled", reason)

    def test_stale_tree_hash_is_no_data_red(self):
        for e in self.fx.entries:
            if e["row"]["host"] == "claude":
                e["row"]["plugin_tree_sha256"] = "0" * 64
        state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("RED", reason)
        self.assertIn("stale", reason)

    def test_forged_deny_record_is_never_certified(self):
        self.flip_to_deny()
        self.entry("claude", "guarded_write")["row"]["verdict"] = "allow"   # the label disagrees with the bytes
        state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertTrue(reason.startswith("RED"), reason)

    # REQ-HG1-06 --------------------------------------------------------------------------------------------------------

    def test_missing_installed_hook_file_is_no_data(self):
        self.fx.write_evidence()
        os.unlink(self.fx.hooks["codex"][0])
        state, reason = self.state("codex", write=False)
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("is gone", reason)

    # REQ-HG1-07 --------------------------------------------------------------------------------------------------------

    def test_missing_guarded_write_row_is_no_data_naming_it(self):
        self.fx.entries = [e for e in self.fx.entries
                           if not (e["row"]["host"] == "claude" and e["row"]["probe"] == "guarded_write")]
        state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("guarded_write", reason)
        self.assertEqual(self.state("codex")[0], "advisory")   # the other host keeps its own state

    def test_ruled_door_and_verb_never_certify(self):
        self.fx.entries = [e for e in self.fx.entries
                           if not (e["row"]["host"] == "codex" and e["row"]["probe"] in ("door", "verb"))]
        state, reason = self.state("codex")
        self.assertEqual(state, "advisory", reason)   # ruled NO-DATA by the verifier, never a pass, never certified
        self.assertIn("NO-DATA by", reason)

    # edges -------------------------------------------------------------------------------------------------------------

    def test_one_host_only(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] == "claude"]
        self.fx.write_evidence()
        got = self.states()
        self.assertEqual(got["claude"][0], "advisory", got)
        self.assertEqual(got["codex"][0], "NO-DATA")
        self.assertIn("no row from codex", got["codex"][1])

    def test_antigravity_outside_the_deciding_scope_is_no_data(self):
        state, reason = self.state("antigravity")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("experimental", reason)
        self.assertIn("4 row(s) not judged", reason)

    def test_unrecognized_host_is_no_data_not_an_exception(self):
        for host in ("windsurf", "", ["claude"], None, 7):
            state, reason = self.state(host)
            self.assertEqual(state, "NO-DATA", (host, reason))
            self.assertIn("unrecognized host", reason)
        got = verifier.truth_states(self.fx.evidence, self.fx.root, self.now, ("claude", "windsurf"))
        self.assertEqual(got["windsurf"][0], "NO-DATA")

    def test_cursor_rows_never_upgrade_cursor(self):
        self.flip_to_deny()
        extra = []
        for e in self.fx.entries:
            if e["row"]["host"] == "claude":
                copy = {"row": dict(e["row"], host="cursor", run_id="run-cursor-2"),
                        "raw_in": e["raw_in"], "raw_out": e["raw_out"]}
                extra.append(copy)
        self.fx.entries += extra
        self.fx.write_evidence()
        with mock.patch.dict(verifier.EXPECTED_GUARDED_WRITE, {"cursor": "deny"}):
            state, reason = self.state("cursor", write=False)
            self.assertEqual(state, "advisory", reason)
            self.assertEqual(self.states()["cursor"][0], "advisory")
        self.assertEqual(self.state("claude", write=False)[0], "certified")

    def test_torn_line_is_one_reason_for_every_host(self):
        path = self.fx.write_evidence()
        with open(path, "ab") as fh:
            fh.write(b'{"host": "claude"')
        got = self.states()
        for host in ("claude", "codex", "antigravity"):
            self.assertEqual(got[host][0], "NO-DATA")
            self.assertIn("truncated", got[host][1])

    def test_malformed_raw_in_is_no_data(self):
        self.entry("claude", "verb")["raw_in"] = b"[1, 2]"
        state, reason = self.state("claude")
        self.assertEqual(state, "NO-DATA", reason)
        self.assertIn("RED", reason)

    def test_manifest_mismatch_is_no_data(self):
        path = self.fx.write_evidence()
        with open(path + ".sha256", "w") as fh:
            fh.write("0" * 64 + "\n")   # a collector appending while the table is read
        self.assertEqual(self.state("claude", write=False)[0], "NO-DATA")

    def test_two_runs_judges_the_newest_and_repeats_identically(self):
        older = []
        for e in self.fx.entries:
            if e["row"]["host"] == "claude":
                row = dict(e["row"], run_id="run-claude-1", recorded_at=base.stamp(self.now - 900))
                if row["probe"] == "guarded_write":
                    row["verdict"] = "deny"   # an older run's broken row: never judged, the newest run decides
                older.append({"row": row, "raw_in": e["raw_in"], "raw_out": e["raw_out"]})
        self.fx.entries = older + self.fx.entries
        self.fx.write_evidence()
        first = self.states()
        self.assertEqual(first, self.states())   # the same evidence, the same table
        self.assertEqual(first["claude"][0], "advisory")

    def test_hostile_arguments_are_refused(self):
        for bad in (None, "", 0, b"x", [1]):
            with self.assertRaises(verifier.VerifyRefused):
                verifier.host_state(bad, self.fx.root, self.now, "claude")
            with self.assertRaises(verifier.VerifyRefused):
                verifier.truth_states(self.fx.evidence, self.fx.root, self.now, bad)
        self.assertEqual(verifier.truth_states(self.fx.evidence, self.fx.root, self.now, ()), {})
        for now in (True, "1", None, -1):
            with self.assertRaises(verifier.VerifyRefused):
                verifier.host_state(self.fx.evidence, self.fx.root, now, "claude")


class TestTruthTable(_Case):

    def table(self, states, evidence="ev.jsonl"):
        return host_capability.render_truth_table(states, evidence)

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verifier.main(list(argv))
        return code, out.getvalue()

    # REQ-HG1-08 --------------------------------------------------------------------------------------------------------

    def test_one_line_per_host_with_exactly_one_state(self):
        states = {"claude": ("certified", "a denial"), "codex": ("advisory", "no denial"),
                  "antigravity": ("NO-DATA", "nothing"), "cursor": ("advisory", "by rule")}
        lines = self.table(states).splitlines()
        self.assertEqual(len(lines), 6, lines)
        self.assertIn("ev.jsonl", lines[0])
        for line, (host, (state, reason)) in zip(lines[1:5], states.items()):
            words = line.split()
            self.assertEqual(words[0], host)
            self.assertEqual(words[1], state)
            self.assertEqual([w for w in words if w in STATES], [state], line)
            self.assertTrue(line.endswith(reason), line)
        self.assertIn("Brother is not an operating system sandbox.", lines[5])
        self.assertEqual(host_capability.HOST_STATES, STATES)

    def test_edges_none_one_many_and_flattened_reasons(self):
        empty = self.table({}).splitlines()
        self.assertEqual(len(empty), 2)   # header and closing sentence only
        self.assertEqual(len(self.table({"claude": ("NO-DATA", "x")}).splitlines()), 3)
        many = dict((h, ("NO-DATA", "r")) for h in ("claude", "codex", "antigravity", "cursor", "windsurf"))
        self.assertEqual(len(self.table(many).splitlines()), 7)
        text = self.table({"claude": ("NO-DATA", "line one\nline two " + "y" * 300), "codex": ("NO-DATA", ""),
                           "cursor": ("advisory", None)})
        lines = text.splitlines()
        self.assertEqual(len(lines), 5, lines)
        self.assertIn("line one line two", lines[1])
        reason = lines[1].split("NO-DATA", 1)[1].strip()
        self.assertLessEqual(len(reason), 100)
        self.assertTrue(lines[2].endswith("no reason recorded"))
        self.assertTrue(lines[3].endswith("no reason recorded"))
        self.assertEqual(text, self.table({"claude": ("NO-DATA", "line one\nline two " + "y" * 300),
                                           "codex": ("NO-DATA", ""), "cursor": ("advisory", None)}))

    # REQ-HG1-09 --------------------------------------------------------------------------------------------------------

    def test_unrecognized_state_raises_and_prints_nothing(self):
        for bad in ("enforced", "GREEN", "", None, "Certified"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(ValueError):
                    self.table({"claude": ("advisory", "ok"), "codex": (bad, "x")})
            self.assertEqual(out.getvalue(), "")
        for bad in ({"claude": "advisory"}, {"claude": ("advisory",)}, {7: ("advisory", "x")}, None, [("a", "b")]):
            with self.assertRaises(ValueError):
                self.table(bad)

    # REQ-HG1-11 --------------------------------------------------------------------------------------------------------

    def test_enforced_is_never_said_of_a_host_that_is_not_certified(self):
        hostile = ("ok", "hooks enforced here", "Enforced by the fence", "sandbox secure", "SANDBOX  SECURE")
        for state in STATES:
            for reason in hostile:
                text = self.table({"claude": (state, reason), "cursor": ("advisory", "rule")})
                for line in text.splitlines():
                    if line.split()[:2] == ["claude", "certified"]:
                        continue
                    low = " ".join(line.lower().split())
                    self.assertNotIn("enforced", low, (state, reason, line))
                    self.assertNotIn("sandbox secure", low, (state, reason, line))
        for line in self.table({"claude": ("advisory", "a"), "codex": ("NO-DATA", "b")}).splitlines():
            self.assertNotIn("enforced", line.lower())
            self.assertNotIn("shell", line.lower())
            self.assertNotIn("network", line.lower())

    # REQ-HG1-10 --------------------------------------------------------------------------------------------------------

    def test_truth_flag_keeps_the_verify_line_and_exit_code(self):
        absent = os.path.join(self.tmp, "absent.jsonl")
        self.fx.write_evidence()
        for evidence, want in ((self.fx.evidence, 0), (absent, 3)):
            plain = self.main(evidence, "--root", self.fx.root)
            truth = self.main(evidence, "--root", self.fx.root, "--truth")
            self.assertEqual(plain[0], want, plain[1])
            self.assertEqual(truth[0], want, truth[1])
            self.assertTrue(truth[1].startswith(plain[1]), truth[1])
            table = truth[1][len(plain[1]):].splitlines()
            self.assertEqual([line.split()[0] for line in table[1:5]], list(verifier.TRUTH_HOSTS))
        self.assertIn("advisory", truth[1])
        self.fx.find("codex", "door")["row"]["probe"] = "verb"   # a relabelled row: RED
        self.fx.write_evidence()
        plain = self.main(self.fx.evidence, "--root", self.fx.root)
        truth = self.main(self.fx.evidence, "--root", self.fx.root, "--truth")
        self.assertEqual((plain[0], truth[0]), (1, 1), truth[1])
        self.assertTrue(truth[1].startswith(plain[1]))
        self.assertNotIn("certified", truth[1].split("\n", 1)[1].split("certified means", 1)[0])

    # REQ-HG1-12 --------------------------------------------------------------------------------------------------------

    def test_truth_table_on_this_checkout_reads_no_data(self):
        absent = os.path.join(self.tmp, "absent", "HP1-host-live.jsonl")
        code, out = self.main(absent, "--truth")   # --root defaults to this checkout
        self.assertEqual(code, 3, out)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("NO-DATA:"), lines[0])
        rows = dict((line.split()[0], line.split()[1]) for line in lines[2:6])
        self.assertEqual(rows, {"claude": "NO-DATA", "codex": "NO-DATA", "antigravity": "NO-DATA",
                                "cursor": "advisory"})


if __name__ == "__main__":
    unittest.main()
