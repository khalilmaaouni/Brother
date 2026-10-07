#!/usr/bin/env python3
"""Tests for scripts/host_live_claude.py (HP1.e, spec docs/plan/specs/HP1.md).

Every case builds its own HOME, binaries, stream and trace under a temp folder: nothing is written under ~/.claude.

Run: python3 scripts/test_host_live_claude.py TestHostLiveClaude -v
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_live_claude as hc  # noqa: E402

HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
GUARD = 'python3 "%s/hooks/hook_guard.py" brothermode %s "--matcher=" python3 "%s" %s'


def _bundle_scripts():
    return hc.shipped_scripts(hc.BUNDLE)


def _command(script, event="SessionStart"):
    return GUARD % (hc.BUNDLE, event, script, "x")


def _line(event, script, hook_id, subtype="hook_response", **extra):
    doc = {"type": "system", "subtype": subtype, "hook_event": event, "command": _command(script, event), "hook_id": hook_id}
    doc.update(extra)
    return json.dumps(doc).encode() + b"\n"


REAL = os.path.join(HERE, "fixtures", "hp1-real-2026-10-04")


def _count_sessions(rows, event):
    counts = {}
    for row in rows:
        if row["event"] == event:
            sid = row["raw_in"]["session_id"]
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def _row(event, run_id="r1"):
    return {"event": event, "run_id": run_id}


class TestHostLiveClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hp1e-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.realpath(self.tmp)
        self.script = _bundle_scripts()[0]

    def _install(self, version, name="claude", build=None):
        between = (build,) if build else ()
        folder = os.path.join(self.home, *hc.INSTALL_PARTS, version, *between, "claude.app", "Contents", "MacOS")
        os.makedirs(folder)
        path = os.path.join(folder, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def _exe(self, folder, name="claude"):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    # resolve_claude
    def test_newest_install_wins(self):
        self._install("2.1.9")
        newest = self._install("2.1.284")
        self.assertEqual(hc.resolve_claude({"HOME": self.home, "PATH": ""}), newest)

    def test_install_with_a_build_hash_folder_is_found_and_the_version_still_decides(self):
        # 2026-10-04: the desktop install keeps <version>/<build hash>/claude.app; the 2026-10-03 reading had no hash
        self._install("2.1.284", build="4819fdb9b264")
        newest = self._install("2.1.286", build="f2326db61802")
        self.assertEqual(hc.resolve_claude({"HOME": self.home, "PATH": ""}), newest)
        flat = self._install("2.1.300")   # a newer version in the flat layout wins over the hashed one
        self.assertEqual(hc.resolve_claude({"HOME": self.home}), flat)

    def test_binary_is_never_found_on_path_alone(self):
        older = self._exe(os.path.join(self.home, "pathbin"))
        self.assertIsNone(hc.resolve_claude({"HOME": self.home, "PATH": os.path.dirname(older)}))

    def test_named_variable_beats_install_and_must_lie_under_allowed_root(self):
        self._install("2.1.284")
        outside = self._exe(os.path.join(self.home, "elsewhere"))
        env = {"HOME": self.home, "HP1_CLAUDE_BIN": outside}
        self.assertIsNone(hc.resolve_claude(env))
        env["HP1_HOST_ALLOWED_ROOTS"] = os.path.dirname(outside)
        self.assertEqual(hc.resolve_claude(env), outside)
        self.assertEqual(hc.resolve_claude({"HOME": self.home, "CLAUDE_BIN": outside, "HP1_HOST_ALLOWED_ROOTS": os.path.dirname(outside)}), outside)

    def test_unusable_named_variable_does_not_fall_through(self):
        self._install("2.1.284")
        self.assertIsNone(hc.resolve_claude({"HOME": self.home, "HP1_CLAUDE_BIN": os.path.join(self.home, "missing")}))

    def test_symlink_out_of_root_is_refused(self):
        outside = self._exe(os.path.join(self.home, "elsewhere"))
        folder = os.path.join(self.home, *hc.INSTALL_PARTS, "2.1.1", "claude.app", "Contents", "MacOS")
        os.makedirs(folder)
        os.symlink(outside, os.path.join(folder, "claude"))
        self.assertIsNone(hc.resolve_claude({"HOME": self.home}))

    def test_resolve_hostile_env(self):
        for bad in HOSTILE:
            self.assertIsNone(hc.resolve_claude(bad))
        for bad in HOSTILE:
            self.assertIsNone(hc.resolve_claude({"HOME": bad}))
            self.assertIsNone(hc.resolve_claude({"HOME": self.home, "HP1_CLAUDE_BIN": bad}))

    # claude_argv
    def test_argv_shape_and_bare_refused(self):
        argv = hc.claude_argv("/a/claude", "/a/bundle", "brother status")
        self.assertEqual(argv, ["/a/claude", "--print", "--output-format", "stream-json", "--verbose", "--include-hook-events",
                                "--plugin-dir", "/a/bundle", "brother status"])
        for args in (("/a/claude", "/a/bundle", "--bare"), ("/a/--bare", "/a/b", "x")):
            if args[2] == "--bare":
                with self.assertRaises(hc.ClaudeRefused):
                    hc.claude_argv(*args)
        for bad in HOSTILE:
            if isinstance(bad, str) and bad.strip():
                continue
            with self.assertRaises(hc.ClaudeRefused):
                hc.claude_argv(bad, "/a/b", "x")
            with self.assertRaises(hc.ClaudeRefused):
                hc.claude_argv("/a/claude", bad, "x")
            with self.assertRaises(hc.ClaudeRefused):
                hc.claude_argv("/a/claude", "/a/b", bad)
        with self.assertRaises(hc.ClaudeRefused):
            hc.claude_argv("claude", "/a/b", "x")

    # parse_hook_events
    def test_parse_keeps_only_allowlisted_commands(self):
        stream = (_line("SessionStart", self.script, "h1") + _line("SessionStart", "/usr/bin/other.py", "h2")
                  + b'{"type":"assistant","subtype":"text"}\n' + b"not json\n")
        events = hc.parse_hook_events(stream)
        self.assertEqual([(e["event"], e["script"], e["host_reported"]) for e in events], [("SessionStart", self.script, "ok")])

    def test_parse_one_event_per_hook_id(self):
        stream = _line("Stop", self.script, "h1", subtype="hook_started") + _line("Stop", self.script, "h1")
        self.assertEqual(len(hc.parse_hook_events(stream)), 1)

    def test_parse_line_without_fields_is_no_data(self):
        events = hc.parse_hook_events(b'{"subtype":"hook_response","hook_id":"h"}\n')
        self.assertEqual([e["host_reported"] for e in events], ["no_data"])
        self.assertEqual(hc.crosscheck([_row("Stop")], events)[0], False)
        self.assertIn("NO-DATA", hc.crosscheck([_row("Stop")], events)[1])

    def test_truncated_last_line_is_ignored_and_counted(self):
        whole = _line("Stop", self.script, "h1")
        stream = whole + _line("Stop", self.script, "h2")[:-30]
        self.assertEqual(len(hc.parse_hook_events(stream)), 1)
        self.assertEqual(hc.stream_truncated(stream), 1)
        self.assertEqual(hc.stream_truncated(whole), 0)

    def test_parse_hostile_stream(self):
        for bad in HOSTILE:
            if isinstance(bad, bytes):
                continue
            with self.assertRaises(hc.ClaudeRefused):
                hc.parse_hook_events(bad)
        self.assertEqual(hc.parse_hook_events(b'[1]\n"x"\n\xff\xfe\n'), [])
        with self.assertRaises(hc.ClaudeRefused):
            hc.parse_hook_events(b"", plugin_root=self.home)

    # crosscheck
    def _ev(self, event, run_id=None):
        doc = {"host_reported": "ok", "event": event, "shipped": None, "command": "c"}   # the old shape, a command
        if run_id:
            doc["run_id"] = run_id
        return doc

    def test_crosscheck_agree(self):
        ok, why = hc.crosscheck([_row("Stop"), _row("Stop"), _row("SessionStart")], [self._ev("SessionStart"), self._ev("Stop"), self._ev("Stop")])
        self.assertTrue(ok, why)

    def test_crosscheck_count_mismatch_is_red(self):
        ok, why = hc.crosscheck([_row("Stop"), _row("Stop")], [self._ev("Stop")])
        self.assertFalse(ok)
        self.assertIn("RED", why)
        self.assertIn("Stop", why)

    def test_stream_without_shim_rows_is_red_and_named(self):
        ok, why = hc.crosscheck([], [self._ev("Stop")])
        self.assertFalse(ok)
        self.assertIn("RED", why)
        self.assertIn("PATH", why)

    def test_shim_rows_without_stream_events_is_red(self):
        ok, why = hc.crosscheck([_row("Stop")], [])
        self.assertFalse(ok)
        self.assertIn("RED", why)

    def test_nothing_at_all_is_no_data(self):
        ok, why = hc.crosscheck([], [])
        self.assertFalse(ok)
        self.assertIn("NO-DATA", why)

    def test_two_runs_judged_per_run_id(self):
        rows = [_row("Stop", "a"), _row("Stop", "b"), _row("Stop", "b")]
        good = [self._ev("Stop", "a"), self._ev("Stop", "b"), self._ev("Stop", "b")]
        self.assertTrue(hc.crosscheck(rows, good)[0])
        swapped = [self._ev("Stop", "a"), self._ev("Stop", "a"), self._ev("Stop", "b")]   # same total, wrong run
        ok, why = hc.crosscheck(rows, swapped)
        self.assertFalse(ok)
        self.assertIn("run a", why)
        ok, why = hc.crosscheck(rows, [self._ev("Stop")] * 3)
        self.assertFalse(ok)
        self.assertIn("NO-DATA", why)

    def test_crosscheck_hostile_input(self):
        for bad in HOSTILE:
            if isinstance(bad, list):
                continue
            self.assertFalse(hc.crosscheck(bad, [])[0])
            self.assertFalse(hc.crosscheck([], bad)[0])
        for bad in HOSTILE:
            self.assertFalse(hc.crosscheck([bad], [self._ev("Stop")])[0])
            self.assertFalse(hc.crosscheck([_row("Stop")], [bad])[0])
            self.assertFalse(hc.crosscheck([{"event": bad, "run_id": "r"}], [self._ev("Stop")])[0])
            self.assertFalse(hc.crosscheck([_row("Stop")], [{"host_reported": "ok", "event": bad}])[0])
        self.assertFalse(hc.crosscheck([_row("Stop")], [{"host_reported": "no_data", "event": None}])[0])
        for shipped in ("4", True, 1.0):   # absent or not an integer: the shipped set is unknown, never assumed
            ev = {"host_reported": "ok", "event": "Stop", "command": "c", "shipped": shipped}
            self.assertIn("ships", hc.crosscheck([_row("Stop")], [ev])[1])
        self.assertIn("ships", hc.crosscheck([_row("Stop")], [{"host_reported": "ok", "event": "Stop", "command": "c"}])[1])

    # the real stream: Claude Code 2.1.286, run hp1-claude-20261004T111227Z-14772. Stderr stripped; the home path
    # (<HOME>) and the account name (also in the dash encoded transcript folder, -HOME) replaced in every string;
    # raw_out_sha256 of the shim rows recomputed over the replaced stdout, so stream and shim still bind byte for byte
    def _real(self, name):
        with open(os.path.join(REAL, name), "rb") as fh:
            return fh.read()

    def test_real_2_1_286_stream_is_read_and_agrees_with_its_witness_rows(self):
        stream = self._real("claude-stream.jsonl")
        rows = [json.loads(line) for line in self._real("claude-trace-rows.jsonl").splitlines()]
        events = hc.parse_hook_events(stream)
        self.assertEqual(len(events), 12)   # 4 SessionStart hooks x 3 sessions, started and response lines share a hook_id
        self.assertEqual({(e["host_reported"], e["event"], e["command"]) for e in events}, {("ok", "SessionStart", None)})
        by_session = {}
        for e in events:
            by_session[e["session_id"]] = by_session.get(e["session_id"], 0) + 1
        self.assertEqual(sorted(by_session.values()), [4, 4, 4])
        self.assertEqual(by_session, _count_sessions(rows, "SessionStart"))
        for e in events:
            e["run_id"] = rows[0]["run_id"]
        self.assertEqual(hc.crosscheck(rows, events), (True, "agree for 1 run(s)"))
        self.assertEqual(len(hc.stream_errors(stream)), 3)
        self.assertTrue(all(e.startswith("Not logged in") for e in hc.stream_errors(stream)))

    def test_real_stream_with_one_hook_line_lost_is_red(self):
        lines = self._real("claude-stream.jsonl").splitlines(True)
        rows = [json.loads(line) for line in self._real("claude-trace-rows.jsonl").splitlines()]
        events = hc.parse_hook_events(b"".join(lines[1:]))   # the first hook's started line gone, its response kept
        self.assertEqual(len(events), 12)
        cut = [line for line in lines if b'"hook_id": "2b33aee9' not in line]   # every line of one hook gone
        events = hc.parse_hook_events(b"".join(cut))
        for e in events:
            e["run_id"] = rows[0]["run_id"]
        ok, why = hc.crosscheck(rows, events)
        self.assertFalse(ok)
        self.assertIn("SessionStart: shim 12, stream 11", why)

    def _real_run(self):
        lines = self._real("claude-stream.jsonl").splitlines(True)
        rows = [json.loads(line) for line in self._real("claude-trace-rows.jsonl").splitlines()]
        return lines, rows

    def _judged(self, lines, rows):
        events = hc.parse_hook_events(b"".join(lines))
        for e in events:
            e["run_id"] = rows[0]["run_id"]
        return hc.crosscheck(rows, events)

    def _foreign(self, sid, stdout):
        hook = {"type": "system", "hook_id": "f-1", "hook_name": "SessionStart:startup", "hook_event": "SessionStart",
                "session_id": sid}
        return [json.dumps(dict(hook, subtype="hook_started")).encode() + b"\n",
                json.dumps(dict(hook, subtype="hook_response", stdout=stdout, exit_code=0)).encode() + b"\n"]

    def test_shipped_counts_read_the_candidates_hook_file(self):
        counts = hc.shipped_counts(hc.BUNDLE)
        self.assertEqual((counts["SessionStart"], counts["Stop"]), (4, 3))
        self.assertNotIn("PreToolUse", counts)   # a matcher event fires per tool call, never a fixed set

    def test_real_stream_with_a_shipped_hook_swapped_for_a_foreign_one_is_red(self):
        lines, rows = self._real_run()
        sid = json.loads(lines[0])["session_id"]
        swapped = [l for l in lines if b'"hook_id": "2b33aee9' not in l] + self._foreign(sid, "foreign\n")
        ok, why = self._judged(swapped, rows)
        self.assertFalse(ok)
        self.assertIn("RED: the stream's hook answers are not the answers the shim recorded", why)

    def test_real_stream_and_shim_both_missing_one_shipped_hook_is_red(self):
        lines, rows = self._real_run()
        gone = json.loads(lines[4])   # one hook's response: drop it and its shim row, so every count still agrees
        lines = [l for l in lines if gone["hook_id"].encode() not in l]
        rows = list(rows)
        rows.remove(next(r for r in rows if r["raw_in"]["session_id"] == gone["session_id"] and r["event"] == "SessionStart"
                         and r["raw_out_sha256"] == hashlib.sha256(gone["stdout"].encode()).hexdigest()))
        ok, why = self._judged(lines, rows)
        self.assertFalse(ok)
        self.assertIn("SessionStart in session %s: stream 3, shipped 4" % gone["session_id"], why)

    def test_real_stream_and_shim_both_carrying_a_foreign_hook_is_red(self):
        lines, rows = self._real_run()
        sid = json.loads(lines[0])["session_id"]
        extra = dict(rows[0], raw_out_sha256=hashlib.sha256(b"foreign\n").hexdigest(), exit_code=0)
        ok, why = self._judged(lines + self._foreign(sid, "foreign\n"), rows + [extra])
        self.assertFalse(ok)
        self.assertIn("stream 5, shipped 4", why)

    def test_a_fixed_set_event_without_session_or_answer_is_no_data(self):
        lines, rows = self._real_run()
        no_sid = [l.replace(b'"session_id"', b'"sid"') for l in lines]
        self.assertIn("names no session_id", self._judged(no_sid, rows)[1])
        no_answer = [l for l in lines if b"hook_response" not in l]
        self.assertIn("neither a command nor a response", self._judged(no_answer, rows)[1])
        rows = [dict(r, raw_out_sha256=None) if r["event"] == "SessionStart" else r for r in rows]
        self.assertIn("lacks the raw_out_sha256", self._judged(lines, rows)[1])

    def test_hook_line_with_event_but_no_id_and_no_command_is_no_data(self):
        events = hc.parse_hook_events(b'{"type":"system","subtype":"hook_started","hook_event":"SessionStart"}\n')
        self.assertEqual([e["host_reported"] for e in events], ["no_data"])
        events = hc.parse_hook_events(b'{"type":"system","subtype":"hook_started","hook_event":"SessionStart","hook_id":""}\n')
        self.assertEqual([e["host_reported"] for e in events], ["no_data"])

    def test_command_less_line_is_counted_by_hook_id(self):
        line = b'{"type":"system","subtype":"hook_started","hook_event":"Stop","hook_id":"%s"}\n'
        events = hc.parse_hook_events(line % b"a" + line % b"a" + line % b"b")
        self.assertEqual([(e["host_reported"], e["hook_id"]) for e in events], [("ok", "a"), ("ok", "b")])

    def test_session_end_is_left_out_on_both_sides(self):
        rows = [_row("SessionStart"), _row("SessionEnd"), _row("SessionEnd")]
        self.assertTrue(hc.crosscheck(rows, [self._ev("SessionStart")])[0])
        self.assertTrue(hc.crosscheck(rows, [self._ev("SessionStart"), self._ev("SessionEnd")])[0])
        ok, why = hc.crosscheck([_row("SessionEnd")], [self._ev("SessionEnd")])
        self.assertFalse(ok)
        self.assertIn("NO-DATA", why)
        ok, why = hc.crosscheck([_row("SessionEnd")], [self._ev("Stop")])
        self.assertFalse(ok)
        self.assertIn("RED", why)

    def test_stream_errors_reads_only_error_results(self):
        stream = (b'{"type":"result","is_error":false,"result":"fine"}\n{"type":"result","is_error":true}\n'
                  b'{"type":"assistant","is_error":true,"result":"x"}\n')
        self.assertEqual(hc.stream_errors(stream), ["is_error with no result text"])
        with self.assertRaises(hc.ClaudeRefused):
            hc.stream_errors("not bytes")

    # run_claude_session
    def test_session_reads_stream_beside_trace(self):
        trace = os.path.join(self.home, "trace", "trace.jsonl")
        os.makedirs(os.path.dirname(trace))
        with open(trace, "w") as fh:
            fh.write(json.dumps(_row("PreToolUse", "run9")) + "\n")
        with self.assertRaises(hc.ClaudeRefused):
            hc.run_claude_session(self.home, os.path.dirname(HERE), trace, 60)
        with open(hc.claude_stream_path(trace), "wb") as fh:
            fh.write(_line("PreToolUse", self.script, "h1"))
        events = hc.run_claude_session(self.home, os.path.dirname(HERE), trace, 60)
        self.assertEqual([e["run_id"] for e in events], ["run9"])
        self.assertTrue(hc.crosscheck([_row("PreToolUse", "run9")], events)[0])
        with open(hc.claude_stream_path(trace), "wb") as fh:   # Stop ships 3 hooks: one fire, no session, is NO-DATA
            fh.write(_line("Stop", self.script, "h1"))
        events = hc.run_claude_session(self.home, os.path.dirname(HERE), trace, 60)
        self.assertIn("NO-DATA", hc.crosscheck([_row("Stop", "run9")], events)[1])
        for bad in HOSTILE:
            with self.assertRaises(hc.ClaudeRefused):
                hc.run_claude_session(self.home, os.path.dirname(HERE), trace, bad)


if __name__ == "__main__":
    unittest.main()
