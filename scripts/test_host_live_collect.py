#!/usr/bin/env python3
"""Tests for the collector of scripts/host_live_proof.py (HP1.f, spec docs/plan/specs/HP1.md section 10).

The fixture is a finished run per host as `--run` leaves it in a throwaway home: the witness's trace rows
(hp1.trace.v1, written with the real writer host_live_proof.write_row, raw bytes beside them), the session record
`--run` writes, the Codex and Claude Code envelopes and the Claude Code stream beside the trace, the shim, and a
candidate checkout written as git objects by hand (test_host_live_verify's GitRepo and Fixture, reused). Each case
runs the real collector and then the real verifier on what it wrote, one condition per case. Every case has its own
temp folder and HOME: nothing is written under ~/.claude.

Run: python3 scripts/test_host_live_collect.py TestCollector -v
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_live_claude as claude  # noqa: E402
import host_live_proof as proof  # noqa: E402
import host_live_verify as verifier  # noqa: E402
import test_host_live_verify as tv  # noqa: E402  the candidate checkout and the per host rows, built once per case

WITNESS_KEYS = proof.TRACE_KEYS   # what host_hook_trace._record writes and the collector carries verbatim


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def envelope_bytes(argv, code, stdout, effect=None):
    body = {"argv": argv, "exit_code": code, "stdout_b64": base64.b64encode(stdout).decode("ascii")}
    if effect is not None:
        body["effect"] = effect
    return json.dumps(body, sort_keys=True).encode("utf-8")


def stream_line(event, command, hook_id, session_id="s1"):
    doc = {"type": "system", "subtype": "hook_response", "hook_event": event, "command": command, "hook_id": hook_id,
           "session_id": session_id}
    return json.dumps(doc).encode("utf-8") + b"\n"


class Runs(object):
    """One finished run per host, laid out the way --run leaves a throwaway home, from test_host_live_verify's rows.
    Antigravity holds the door and verb rows only (the owner's panel session recorded two fires): under 1.1.0 it is
    reported, never judged, and under the 1.1.1 bar it is NO-DATA."""

    def __init__(self, tmp, now):
        self.tmp, self.now = tmp, now
        self.home_root = os.path.join(tmp, "home")
        os.makedirs(self.home_root)
        self.fx = tv.Fixture(tmp, now)
        self.root = self.fx.root
        self.evidence = os.path.join(tmp, "collected", "HP1-host-live.jsonl")
        self.homes, self.traces = {}, {}
        for host in verifier.REQUIRED_HOSTS:
            self.lay_out(host)

    def entries(self, host):
        rows = [e for e in self.fx.entries if e["row"]["host"] == host]
        if host == "antigravity":
            rows = [e for e in rows if e["row"]["probe"] in ("door", "verb")]
        return rows

    def trace_row(self, row, cwd):
        """The row the witness writes for this fire: the hp1.v1 keys the witness itself records, nothing derived."""
        out = dict((key, row[key]) for key in WITNESS_KEYS if key in row)
        out.update({"schema": proof.TRACE_SCHEMA, "probe": row["probe"], "stderr_sha256": sha(b"e" * row["stderr_len"]),
                    "parent_chain_error": "", "cwd": cwd})
        return out

    def lay_out(self, host):
        run_id = "run-%s-2" % host
        home = os.path.join(self.home_root, ".claude", "brother-scratch", run_id)
        folder = os.path.join(home, "trace")
        os.makedirs(folder)
        trace = os.path.join(folder, "trace.jsonl")
        self.homes[host], self.traces[host] = home, trace
        entries = self.entries(host)
        for e in entries:
            proof.write_row(trace, self.trace_row(e["row"], self.fx.workspace), e["raw_in"], e["raw_out"])
        first = entries[0]["row"]
        record = {"schema": proof.SESSION_SCHEMA, "host": host, "run_id": run_id, "home": home, "source": self.root,
                  "host_bin": self.fx.bins[host], "host_bin_realpath": self.fx.bins[host],
                  "host_version": first["host_version"], "host_roots": first["host_roots"],
                  "shim_dir": self.fx.shim_dir, "shim_path": os.path.join(self.fx.shim_dir, "python3"),
                  "real_python": sys.executable, "plugin_root": self.fx.roots[host], "hooks_path": self.fx.hooks[host][0],
                  "target": self.fx.target, "workspace": self.fx.workspace, "trace_path": trace, "exists_before": False,
                  "plugin_tree_sha256": self.fx.tree_hash, "tool_sha256": dict(self.fx.tools),
                  "recorded_at": tv.stamp(self.now - 700)}
        if host == "codex":
            record["plugin_install"] = self.fx.install_root()   # what --run measures after the Codex install
            record["plugin_install_sha256"] = self.fx.install_sha()
        proof.write_session(folder, record)
        for e in entries:
            row = e["row"]
            if "model_turn" in row:
                effect = row.get("effect") if row["probe"] == "guarded_write" else None
                write(os.path.join(folder, "%s-%s.json" % (run_id, row["probe"])),
                      envelope_bytes(row["model_turn"]["argv"], 0, tv.TURN_OK if host == "codex" else tv.STREAM_OK, effect))
        if host == "codex":
            write(os.path.join(folder, run_id + proof.LOGIN_SUFFIX),
                  envelope_bytes([self.fx.bins[host], "login", "status"], 0, b"Logged in using ChatGPT\n"))
        if host == "claude":   # the door session's transcript, answered (owner ruling 2026-10-05 hp1-claude-door)
            write(os.path.join(folder, run_id + "-door.json"), envelope_bytes(self.fx.claude_argv(), 0, tv.CC_DOOR_OK))
            hooks = tv.CC_HOOKS["hooks"]
            lines = [stream_line(e["row"]["event"], hooks[e["row"]["event"]][0]["hooks"][0]["command"], "h%d" % n,
                                 "s%d" % n) for n, e in enumerate(entries, 1)]   # one session per probe, as --run starts them
            write(claude.claude_stream_path(trace), b"".join(lines))

    def collect(self, host):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proof.main(["--collect", host, "--home", self.homes[host], "--source", self.root,
                               "--evidence", self.evidence])
        return code, out.getvalue().splitlines()

    def rows(self):
        rows, problem = verifier.load_rows(self.evidence)
        assert problem == "", problem
        return rows


class TestCollector(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hp1f-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = int(time.time())
        self.runs = Runs(self.tmp, self.now)
        patch = mock.patch.dict(os.environ, {"HOME": self.runs.home_root})
        patch.start()
        self.addCleanup(patch.stop)

    # the baseline: three runs collected, the verifier judges them --------------------------------------------------

    def test_collected_rows_are_green_for_claude_and_codex_and_no_data_for_antigravity(self):
        code, lines = self.runs.collect("claude")
        self.assertTrue(lines[0].startswith("COLLECTED: claude run run-claude-2: 5 row(s)"), lines)
        self.assertEqual((code, lines[1][:8]), (3, "NO-DATA:"), lines)   # the verifier: no codex row yet
        self.assertIn("no row from codex", lines[1])
        code, lines = self.runs.collect("codex")
        self.assertTrue(lines[0].startswith("COLLECTED: codex run run-codex-2: 5 row(s)"), lines)
        self.assertEqual((code, lines[1][:6]), (0, "GREEN:"), lines)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 0), lines[1])
        code, lines = self.runs.collect("antigravity")
        self.assertTrue(lines[0].startswith("COLLECTED: antigravity run run-antigravity-2: 2 row(s)"), lines)
        self.assertEqual((code, lines[1][:6]), (0, "GREEN:"), lines)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 2), lines[1])
        rows = self.runs.rows()
        self.assertEqual(len(rows), 12)
        self.assertEqual({r["schema"] for r in rows}, {"hp1.v1"})
        self.assertEqual(sorted((r["host"], r["probe"]) for r in rows), sorted(
            [("claude", "door"), ("claude", "verb"), ("claude", "guarded_write"), ("claude", "other"), ("claude", "other"),
             ("codex", "door"), ("codex", "verb"),
             ("codex", "guarded_write"), ("codex", "other"), ("codex", "other"), ("antigravity", "door"), ("antigravity", "verb")]))
        folder = os.path.dirname(self.runs.evidence)
        for row in rows:   # every file a row names sits beside the evidence and hashes as the row says
            for name, digest in ((row["raw_in_path"], row["raw_in_sha256"]), (row["raw_out_path"], row["raw_out_sha256"]),
                                 (row["shim_path"], row["shim_sha256"])):
                self.assertEqual(sha(read(os.path.join(folder, name))), digest, name)
            for key in ("signed_in", "model_turn"):
                if key in row:
                    self.assertEqual(sha(read(os.path.join(folder, row[key]["envelope_path"]))), row[key]["envelope_sha256"])
            if row["host"] == "claude":
                self.assertEqual(sha(read(os.path.join(folder, row["stream_path"]))), row["stream_sha256"])
        with contextlib.redirect_stdout(io.StringIO()):   # every host decides (the 1.1.1 bar): Antigravity owes its rows
            under_1_1_1 = verifier.verify(self.runs.evidence, self.runs.root, self.now, verifier.REQUIRED_HOSTS)
        self.assertEqual((under_1_1_1[0], under_1_1_1[1][:8]), (3, "NO-DATA:"), under_1_1_1)
        self.assertIn("antigravity run run-antigravity-2 has no guarded_write", under_1_1_1[1])
        code, lines = self.runs.collect("codex")   # collected twice: the byte identical lines count once
        self.assertEqual((code, lines[1][:6]), (0, "GREEN:"), lines)
        self.assertEqual(len(self.runs.rows()), 12)

    # the guards, one fixture each ------------------------------------------------------------------------------------

    def test_a_tampered_raw_file_is_refused_and_nothing_of_the_run_is_written(self):
        trace = self.runs.traces["codex"]
        rows = proof._read_rows(trace)
        raw = os.path.join(os.path.dirname(trace), rows[1]["raw_in_path"])
        data = bytearray(read(raw))
        data[-2] ^= 0x01
        write(raw, bytes(data))   # same length, other bytes
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:4]), (1, "RED:"), lines)
        self.assertIn("does not hash to its sha256 name", lines[0])
        self.assertEqual(len(lines), 1)   # no verifier line after a refusal
        self.assertFalse(os.path.exists(self.runs.evidence))

    def test_rows_of_another_run_are_never_collected(self):
        trace = self.runs.traces["codex"]
        foreign = proof._read_rows(trace)[0]
        foreign = dict((k, v) for k, v in foreign.items() if not k.startswith("raw_") and k != "truncated")
        foreign["run_id"] = "run-codex-1"
        entry = self.runs.entries("codex")[0]
        proof.write_row(trace, foreign, entry["raw_in"], entry["raw_out"])   # a complete row of an older run
        code, lines = self.runs.collect("codex")
        self.assertTrue(lines[0].startswith("COLLECTED: codex run run-codex-2: 5 row(s)"), lines)
        self.assertIn("skipped 1 row(s) of other runs", lines[0])
        self.assertEqual({r["run_id"] for r in self.runs.rows()}, {"run-codex-2"})
        self.assertEqual(len(self.runs.rows()), 5)

    def test_a_field_that_cannot_be_derived_is_no_data_and_the_row_is_not_written(self):
        folder = os.path.dirname(self.runs.traces["codex"])
        session = os.path.join(folder, "run-codex-2" + proof.SESSION_SUFFIX)
        record = json.loads(read(session).decode("utf-8"))
        record["host_version"] = "no_data"   # the binary printed no version: never invented
        write(session, json.dumps(record).encode("utf-8"))
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("host_version", lines[0])
        self.assertFalse(os.path.exists(self.runs.evidence))
        record["host_version"] = "codex-cli 0.157.0"
        write(session, json.dumps(record).encode("utf-8"))
        os.unlink(os.path.join(folder, "run-codex-2-verb.json"))   # one envelope gone: that row alone is NO-DATA
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("run-codex-2-verb.json", lines[0])
        self.assertEqual(sorted(r["probe"] for r in self.runs.rows()), ["door", "guarded_write", "other", "other"])
        os.unlink(session)   # no session record at all: nothing can be derived
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("no session record", lines[0])

    def test_a_run_recorded_on_an_older_tree_is_not_collected_after_the_tree_changed(self):
        fx = self.runs.fx
        fx.files["plugin/VERSION"] = ("100644", b"1.1.1\n")   # a later landing under plugin, after the run
        fx.repo.commit(fx.files, self.now - 300)
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("plugin_tree_sha256", lines[0])
        self.assertIn(fx.tree_hash[:12], lines[0])   # the value the run recorded
        self.assertIn(verifier.candidate_facts(self.runs.root)["plugin_tree_sha256"][:12], lines[0])   # and the value now
        self.assertFalse(os.path.exists(self.runs.evidence))
        fx.files["plugin/VERSION"] = ("100644", b"1.1.0\n")
        fx.repo.commit(fx.files, self.now - 200)   # the tree reads as it did at run time: collected again
        code, lines = self.runs.collect("codex")
        self.assertTrue(lines[0].startswith("COLLECTED: codex run run-codex-2: 5 row(s)"), lines)
        folder = os.path.dirname(self.runs.traces["codex"])
        session = os.path.join(folder, "run-codex-2" + proof.SESSION_SUFFIX)
        record = json.loads(read(session).decode("utf-8"))
        record["tool_sha256"] = dict(record["tool_sha256"], **{"scripts/host_live_proof.py": sha(b"an older driver")})
        write(session, json.dumps(record).encode("utf-8"))   # a run made with an older tool script
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("tool_sha256", lines[0])

    # the owner ruling of 2026-10-04 hp1-codex-door: the Codex SessionStart rows carry the door run's transcript ----------

    def codex_without_door_rows(self, door_stdout=tv.DOOR_OK):
        """The Codex run as the 2026-10-04 run left it: no door or verb row, the door turn's envelope beside the trace
        (door_stdout None: the envelope absent)."""
        shutil.rmtree(self.runs.homes["codex"])
        self.runs.fx.entries = [e for e in self.runs.fx.entries
                                if not (e["row"]["host"] == "codex" and e["row"]["probe"] in ("door", "verb"))]
        self.runs.lay_out("codex")
        folder = os.path.dirname(self.runs.traces["codex"])
        if door_stdout is not None:
            argv = [self.runs.fx.bins["codex"], "exec", "--json", "--ephemeral", "-C", self.runs.fx.workspace,
                    proof.DOOR_SENTENCE]
            write(os.path.join(folder, "run-codex-2-door.json"), envelope_bytes(argv, 0, door_stdout))
        self.assertEqual(self.runs.collect("claude")[0], 3)   # the verifier: no codex row yet

    def test_the_door_transcript_rides_on_the_codex_session_start_rows_and_decides_green(self):
        self.codex_without_door_rows()
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[1][:6]), (0, "GREEN:"), lines)
        self.assertIn("codex door transcript answered", lines[1])
        rows = [r for r in self.runs.rows() if r["host"] == "codex"]
        carried = [r for r in rows if "door_turn" in r]
        self.assertEqual([r["event"] for r in carried], ["SessionStart"])   # no other event carries it
        ref = carried[0]["door_turn"]
        self.assertEqual(ref["envelope_path"], "run-codex-2-door.json")
        self.assertEqual(sha(read(os.path.join(os.path.dirname(self.runs.evidence), ref["envelope_path"]))),
                         ref["envelope_sha256"])

    def test_no_door_envelope_is_collected_but_never_green(self):
        self.codex_without_door_rows(door_stdout=None)
        code, lines = self.runs.collect("codex")
        self.assertTrue(lines[0].startswith("COLLECTED: codex run run-codex-2: 3 row(s)"), lines)
        self.assertEqual((code, lines[1][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("no transcript shows the door answered", lines[1])

    def test_a_door_envelope_with_no_answer_is_never_green(self):
        self.codex_without_door_rows(door_stdout=tv.TURN_OK)
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[1][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("the door did not route to the status verb", lines[1])

    def test_a_session_with_no_measured_install_is_collected_but_never_green(self):
        self.codex_without_door_rows()
        session = os.path.join(os.path.dirname(self.runs.traces["codex"]), "run-codex-2" + proof.SESSION_SUFFIX)
        record = json.loads(read(session).decode("utf-8"))
        record.pop("plugin_install")   # a session written before the install was measured
        write(session, json.dumps(record).encode("utf-8"))
        code, lines = self.runs.collect("codex")
        self.assertEqual((code, lines[1][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("recorded no installed plugin root", lines[1])
        self.assertFalse([r for r in self.runs.rows() if "plugin_install" in r])

    def test_the_measured_install_rides_on_the_session_start_rows(self):
        self.codex_without_door_rows()
        self.runs.collect("codex")
        carried = [r for r in self.runs.rows() if "plugin_install" in r]
        self.assertEqual([(r["event"], r["plugin_install"]) for r in carried],
                         [("SessionStart", self.runs.fx.install_root())])

    def test_the_install_is_measured_as_exactly_one_version_folder(self):
        home = os.path.join(self.tmp, "codex-measured")
        cache = os.path.join(home, "plugins", "cache", "brother", "brother")
        with self.assertRaises(proof.HP1Refused):   # no cache at all: unreadable
            proof.installed_plugin_root(home)
        os.makedirs(cache)
        with self.assertRaises(proof.HP1Refused):   # no version folder
            proof.installed_plugin_root(home)
        os.makedirs(os.path.join(cache, "1.1.0"))
        write(os.path.join(cache, "notes.txt"), b"a file beside it is not a version folder")
        self.assertEqual(proof.installed_plugin_root(home), os.path.join(cache, "1.1.0"))
        os.makedirs(os.path.join(cache, "1.0.9"))
        with self.assertRaises(proof.HP1Refused):   # two versions: which one Codex loads is not known
            proof.installed_plugin_root(home)
        for bad in (None, "", 7):
            with self.assertRaises(proof.HP1Refused):
                proof.installed_plugin_root(bad)

    def test_an_install_reached_through_a_link_is_refused(self):
        home = os.path.join(self.tmp, "codex-linked")
        cache = os.path.join(home, "plugins", "cache", "brother", "brother")
        os.makedirs(cache)
        target = os.path.join(self.runs.fx.workspace, "fake-install")
        os.makedirs(target)
        os.symlink(target, os.path.join(cache, "1.1.0"))   # the version folder is a link into the workspace
        with self.assertRaises(proof.HP1Refused):
            proof.installed_plugin_root(home)
        other = os.path.join(self.tmp, "codex-linked-parent")
        real_brother = os.path.join(self.tmp, "real-brother")
        os.makedirs(os.path.join(real_brother, "1.1.0"))
        os.makedirs(os.path.join(other, "plugins", "cache", "brother"))
        os.symlink(real_brother, os.path.join(other, "plugins", "cache", "brother", "brother"))   # a link higher up
        with self.assertRaises(proof.HP1Refused):
            proof.installed_plugin_root(other)

    def test_the_installed_skills_digest_is_read_from_disk(self):
        root = self.runs.fx.install_root()
        shas = dict((n, sha(read(self.runs.fx.door_skill(n)))) for n in verifier.DOOR_SKILLS)
        self.assertEqual(proof.installed_skills_digest(root), proof.install_digest(root, shas))
        self.assertEqual(proof.installed_skills_digest(root), self.runs.fx.install_sha())   # a faithful install
        skill = self.runs.fx.door_skill()
        copy = write(os.path.join(self.tmp, "skill-copy.md"), read(skill))
        os.unlink(skill)
        os.symlink(copy, skill)
        with self.assertRaises(proof.HP1Refused):   # a door skill reached through a link
            proof.installed_skills_digest(root)
        os.unlink(skill)
        with self.assertRaises(proof.HP1Refused):   # a door skill missing
            proof.installed_skills_digest(root)
        with self.assertRaises(proof.HP1Refused):
            proof.install_digest(root, {"using-brother": 7})

    def test_the_codex_install_step_records_the_measured_install_and_its_digest(self):
        codex_home = os.path.join(self.tmp, "codex-install", ".codex")
        root = os.path.join(codex_home, "plugins", "cache", "brother", "brother", "1.1.0")
        for name in verifier.DOOR_SKILLS:
            write(os.path.join(root, "skills", name, "SKILL.md"), b"installed " + name.encode())
        stub = types.ModuleType("codex_battery")
        stub.setup_home = lambda home: None
        done = subprocess.CompletedProcess([], 0, stdout=b"ok")
        lay = {"codex_home": codex_home, "home": os.path.dirname(codex_home), "workspace": self.runs.fx.workspace}
        with mock.patch.dict(sys.modules, {"codex_battery": stub}), \
                mock.patch.object(proof.subprocess, "run", lambda *a, **k: done):
            got = proof._install_codex(lay, self.runs.root)
        self.assertEqual(got["plugin_install"], root)
        self.assertEqual(got["plugin_install_sha256"], proof.installed_skills_digest(root))

    def test_collect_writes_the_manifest_the_verifier_checks(self):
        self.codex_without_door_rows()
        self.runs.collect("codex")
        manifest = read(self.runs.evidence + proof.MANIFEST_SUFFIX).decode("ascii").strip()
        self.assertEqual(manifest, sha(read(self.runs.evidence)))
        with self.assertRaises(proof.HP1Refused):
            proof.write_manifest(os.path.join(self.tmp, "no-such-evidence.jsonl"))

    def test_a_host_outside_the_ruling_carries_no_door_transcript(self):
        self.codex_without_door_rows()
        with mock.patch.dict(verifier.RULED_NO_DATA, {}, clear=True):
            self.runs.collect("codex")
            self.assertFalse([r for r in self.runs.rows() if "door_turn" in r and r["host"] == "codex"])

    def test_claude_stream_that_disagrees_with_the_witness_is_not_green(self):
        trace = self.runs.traces["claude"]
        stream = claude.claude_stream_path(trace)
        command = tv.CC_HOOKS["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        write(stream, read(stream) + stream_line("PreToolUse", command, "h-extra"))   # one fire more than the witness saw
        code, lines = self.runs.collect("claude")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("do not agree", lines[0])
        self.assertFalse(os.path.exists(self.runs.evidence))
        os.unlink(stream)
        code, lines = self.runs.collect("claude")
        self.assertEqual((code, lines[0][:8]), (3, "NO-DATA:"), lines)
        self.assertIn("stream", lines[0])
        self.assertFalse(os.path.exists(self.runs.evidence))

    def test_the_probe_label_comes_from_the_one_labeller_not_the_witness(self):
        trace = self.runs.traces["codex"]
        rows = proof._read_rows(trace)
        verb = next(r for r in rows if r["probe"] == "verb")
        relabelled = dict((k, v) for k, v in verb.items() if not k.startswith("raw_") and k != "truncated")
        relabelled["probe"] = "door"   # the witness's label says door; the host's own stdin says verb
        entry = next(e for e in self.runs.entries("codex") if e["row"]["probe"] == "verb")
        with open(trace, "wb"):
            pass
        proof.write_row(trace, relabelled, entry["raw_in"], entry["raw_out"])
        code, line = proof.collect(trace, "run-codex-2", self.runs.evidence, self.runs.root, "codex")
        self.assertEqual(code, 0, line)
        self.assertEqual([r["probe"] for r in self.runs.rows()], ["verb"])
        self.assertEqual(proof.probe_of(entry["raw_in"], event="UserPromptSubmit"), "verb")

    def test_a_row_of_another_host_or_schema_in_the_run_is_red(self):
        trace = self.runs.traces["codex"]
        row = proof._read_rows(trace)[0]
        row = dict((k, v) for k, v in row.items() if not k.startswith("raw_") and k != "truncated")
        entry = self.runs.entries("codex")[0]
        proof.write_row(trace, dict(row, host="claude"), entry["raw_in"], entry["raw_out"])
        code, line = proof.collect(trace, "run-codex-2", self.runs.evidence, self.runs.root)
        self.assertEqual((code, line[:4]), (1, "RED:"), line)
        self.assertIn("names host 'claude'", line)
        with open(trace, "wb"):
            pass
        proof.write_row(trace, dict(row, schema="hp1.v1"), entry["raw_in"], entry["raw_out"])
        code, line = proof.collect(trace, "run-codex-2", self.runs.evidence, self.runs.root)
        self.assertEqual((code, line[:4]), (1, "RED:"), line)
        self.assertIn("not a hp1.trace.v1 witness row", line)
        code, line = proof.collect(trace, "run-codex-2", self.runs.evidence, self.runs.root, "claude")
        self.assertEqual((code, line[:4]), (1, "RED:"), line)   # --collect names the wrong host for the run
        self.assertFalse(os.path.exists(self.runs.evidence))

    # the session record --run writes --------------------------------------------------------------------------------

    def test_the_session_record_round_trips_and_the_version_is_what_the_binary_printed(self):
        fake = write(os.path.join(self.tmp, "bin", "host"), b"#!/bin/sh\necho 'fixture-host 9.9'\necho more\n")
        os.chmod(fake, 0o755)
        self.assertEqual(proof.host_version(fake), "fixture-host 9.9")
        silent = write(os.path.join(self.tmp, "bin", "silent"), b"#!/bin/sh\nexit 0\n")
        os.chmod(silent, 0o755)
        failing = write(os.path.join(self.tmp, "bin", "failing"), b"#!/bin/sh\necho 1.0\nexit 1\n")
        os.chmod(failing, 0o755)
        for path in (silent, failing, os.path.join(self.tmp, "bin", "absent"), "relative/host"):
            self.assertEqual(proof.host_version(path), "no_data", path)
        plan = {"host": "codex", "run_id": "run-x", "host_bin": fake, "host_roots": [os.path.dirname(fake)],
                "shim_dir": os.path.join(self.tmp, "shim"), "real_python": sys.executable, "target": "/t/x",
                "trace_path": os.path.join(self.tmp, "t", "trace.jsonl")}
        prepared = {"home": self.tmp, "source": self.runs.root, "shim_path": os.path.join(self.tmp, "shim", "python3"),
                    "plugin_root": "/p", "hooks_path": "/p/hooks.json", "workspace": "/w"}
        binding = proof.candidate_binding(self.runs.root)
        self.assertEqual(binding, {"plugin_tree_sha256": self.runs.fx.tree_hash, "tool_sha256": self.runs.fx.tools})
        self.assertEqual(proof.candidate_binding(os.path.join(self.tmp, "no-such-checkout")),
                         {"plugin_tree_sha256": "no_data", "tool_sha256": "no_data"})   # unreadable: never invented
        record = proof.session_record(plan, prepared, "fixture-host 9.9", binding)
        self.assertEqual([k for k in proof.SESSION_KEYS if k not in record], [])
        self.assertNotIn("plugin_install", record)   # recorded only when the install measured one
        installed = proof.session_record(plan, dict(prepared, plugin_install="/c/plugins/cache/brother/brother/1.1.0"),
                                         "fixture-host 9.9", binding)
        self.assertEqual(installed["plugin_install"], "/c/plugins/cache/brother/brother/1.1.0")
        self.assertEqual((record["host_bin_realpath"], record["exists_before"]), (os.path.realpath(fake), False))
        self.assertEqual((record["plugin_tree_sha256"], record["tool_sha256"]), (self.runs.fx.tree_hash, self.runs.fx.tools))
        with self.assertRaises(proof.HP1Refused):
            proof.session_record(plan, prepared, "v", {"plugin_tree_sha256": "x"})
        folder = os.path.join(self.tmp, "t")
        os.makedirs(folder)
        path = proof.write_session(folder, record)
        self.assertEqual(path, os.path.join(folder, "run-x" + proof.SESSION_SUFFIX))
        self.assertEqual(proof._read_session(folder, "run-x"), (record, ""))
        self.assertIn("names run", proof._read_session(folder, "run-y")[1].replace("no session record", "names run"))
        for bad in ({}, dict(record, schema="x"), dict(record, run_id=7), "x", None):
            with self.assertRaises(proof.HP1Refused):
                proof.write_session(folder, bad)
        with self.assertRaises(proof.HP1Refused):
            proof.session_record({"host": "codex"}, prepared, "v", binding)
        with self.assertRaises(proof.HP1Refused):
            proof.write_session(os.path.join(self.tmp, "absent-folder"), record)

    def test_the_owner_commands_end_with_the_collect_step(self):
        for host in proof.HOSTS:
            home = os.path.join(self.runs.home_root, ".claude", "brother-scratch", "hp1-owner-%s" % host)
            lines = proof.owner_commands(host, home)
            self.assertEqual(lines[-1], "python3 scripts/host_live_proof.py --collect %s --home %s" % (
                host, os.path.realpath(home)), lines)

    def test_collect_needs_a_home_and_refuses_hostile_arguments(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            proof.main(["--collect", "codex"])
        self.assertIn("--home", err.getvalue())
        for bad in tv.HOSTILE:   # refused, or answered NO-DATA / no_data: never a crash, never a row
            try:
                self.assertEqual(proof.collect(bad, bad, bad, bad)[0], 3, bad)
                self.assertEqual(proof.host_version(bad), "no_data", bad)
            except proof.HP1Refused:
                pass
            with self.assertRaises(proof.HP1Refused):
                proof.collect_and_judge(bad, bad, bad, bad, bad)
        with self.assertRaises(proof.HP1Refused):
            proof.collect(self.runs.traces["codex"], "run-codex-2", self.runs.evidence, self.runs.root, "cursor")
        for bad in tv.HOSTILE:
            with self.assertRaises((verifier.VerifyRefused, verifier.VerifyNoData)):
                verifier.candidate_facts(bad)
        facts = verifier.candidate_facts(self.runs.root)
        self.assertEqual((facts["plugin_tree_sha256"], facts["tree_clean"], facts["dirty"]), (self.runs.fx.tree_hash, True, []))
        self.assertEqual(facts["tool_sha256"], self.runs.fx.tools)
        self.assertEqual(facts["hooks_expected_sha256"], dict((h, self.runs.fx.hooks[h][1]) for h in verifier.REQUIRED_HOSTS))
        self.assertFalse(os.path.exists(self.runs.evidence))


if __name__ == "__main__":
    unittest.main()
