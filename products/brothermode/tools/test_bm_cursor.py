#!/usr/bin/env python3
"""Tests for BrotherMode Cursor compatibility mode.

Covers the hook adapter, the mailbox harness, rules/hooks emission, and
the install/uninstall ownership rules. No live Cursor Agent is required;
payload shapes are the documented Cursor contract from 2026-08-10.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import bm_cursor as bc  # noqa: E402
import bm_cursor_hook as hook  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _run(argv, cwd=None, env=None, input_text=None):
    return subprocess.run(
        [sys.executable] + list(argv),
        cwd=cwd or ROOT,
        env=env,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=120,
    )


class TestCursorHookAdapter(unittest.TestCase):
    def test_shell_maps_to_bash(self):
        payload = {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": "echo hi", "working_directory": "/tmp"},
            "conversation_id": "conv-1",
        }
        claude = hook.cursor_to_claude_payload(payload, "preToolUse")
        self.assertEqual(claude["tool_name"], "Bash")
        self.assertEqual(claude["hook_event_name"], "PreToolUse")
        self.assertEqual(claude["session_id"], "conv-1")
        self.assertEqual(claude["cwd"], "/tmp")

    def test_before_shell_execution_shape(self):
        payload = {"command": "ls", "cwd": "/proj", "sandbox": False}
        claude = hook.cursor_to_claude_payload(payload, "beforeShellExecution")
        self.assertEqual(claude["tool_name"], "Bash")
        self.assertEqual(claude["tool_input"]["command"], "ls")
        self.assertEqual(claude["cwd"], "/proj")

    def test_deny_translation(self):
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "owned by other",
            }
        }
        flat = hook.claude_deny_to_cursor(decision)
        self.assertEqual(flat["permission"], "deny")
        self.assertIn("owned", flat["user_message"])

    def test_adapter_fail_open_on_read(self):
        payload = json.dumps({
            "hook_event_name": "preToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
            "cwd": "/tmp",
            "conversation_id": "t1",
        })
        proc = _run(
            [os.path.join(HERE, "bm_cursor_hook.py"), "preToolUse"],
            input_text=payload,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertEqual(body.get("permission"), "allow")

    def test_write_path_keys_normalized(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {"path": "app.py"},
        }
        claude = hook.cursor_to_claude_payload(payload, "preToolUse")
        self.assertEqual(claude["tool_input"]["file_path"], "app.py")


class TestFindCheckout(unittest.TestCase):
    """WBS-70 U3: find_checkout() must see the umbrella plugin layout
    (VERSION-less, nested at runtime/hooks/brothermode/) as well as the
    original flat compat layout, without breaking the contract every
    caller relies on: the return value is a directory callers append
    "tools" (or "VERSION") to themselves."""

    ENV_VARS = ("BROTHERMODE_ROOT", "BROTHER_PLUGIN_ROOT",
                "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
                "BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cursor-checkout-test-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._saved_env = {name: os.environ.get(name) for name in self.ENV_VARS}
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _clear_env(self):
        for name in self.ENV_VARS:
            os.environ.pop(name, None)

    def _touch(self, path):
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")

    def test_umbrella_layout_returns_nested_root_not_tools_tools(self):
        # The installed-bundle shape: no VERSION anywhere, the real
        # checkout nests under runtime/hooks/brothermode/.
        nested = os.path.join(self.tmp, "runtime", "hooks", "brothermode")
        self._touch(os.path.join(nested, "tools", "bm_cursor.py"))
        self._touch(os.path.join(nested, "tools", "bm_store.py"))
        self._clear_env()
        os.environ["BROTHERMODE_ROOT"] = self.tmp
        found = bc.find_checkout()
        self.assertEqual(found, nested)
        # The literal plan ask (return the tools dir itself) would have
        # produced .../tools, and every caller appending "tools" again
        # would land on a nonexistent .../tools/tools path.
        self.assertTrue(found.endswith(os.path.join("hooks", "brothermode")))
        self.assertTrue(os.path.isfile(os.path.join(found, "tools",
                                                     "bm_store.py")))

    def test_flat_compat_layout_still_works(self):
        self._touch(os.path.join(self.tmp, "VERSION"))
        self._touch(os.path.join(self.tmp, "tools", "bm_store.py"))
        self._clear_env()
        os.environ["BROTHERMODE_ROOT"] = self.tmp
        self.assertEqual(bc.find_checkout(), self.tmp)

    def test_local_cursor_plugin_candidate_without_env_var(self):
        # Cursor itself only ever loads a local plugin from
        # ~/.cursor/plugins/local/brother; nothing exports a variable
        # naming it, so find_checkout must look there unprompted.
        fake_home = os.path.join(self.tmp, "home")
        local_plugin = os.path.join(fake_home, ".cursor", "plugins",
                                    "local", "brother")
        nested = os.path.join(local_plugin, "runtime", "hooks",
                              "brothermode")
        self._touch(os.path.join(nested, "tools", "bm_cursor.py"))
        self._touch(os.path.join(nested, "tools", "bm_store.py"))
        self._clear_env()
        # Neutralise the earlier rungs: with no override, plugin_root()
        # falls back to this module's own real on-disk location (a
        # genuine flat checkout) which would match before this candidate
        # is even reached. Point it at nothing so discovery falls through
        # to the local-plugin candidate under test.
        os.environ["BROTHER_PLUGIN_ROOT"] = os.path.join(
            self.tmp, "not-a-real-plugin-root")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = fake_home
        try:
            self.assertEqual(bc.find_checkout(), nested)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    def test_missing_environment_does_not_crash_discovery(self):
        # No BROTHERMODE_ROOT, no plugin-root variable, nothing: every
        # candidate must be produced and checked without raising, and
        # discovery must still land on a real answer (this checkout).
        self._clear_env()
        try:
            found = bc.find_checkout()
        except Exception as exc:  # noqa: BLE001 - the property under test
            self.fail("find_checkout() raised with no env set: %r" % exc)
        self.assertIsNotNone(found)
        self.assertTrue(os.path.isfile(os.path.join(found, "VERSION")))

    def test_broken_first_candidate_does_not_stop_discovery(self):
        # A malformed candidate (VERSION is a directory, bm_store.py is a
        # dangling symlink) must read as "no match", never crash the
        # whole call and take every other caller down with it (the same
        # failure class this file shipped once before, in a different
        # function: a-model-drafted-crash-fix-can-quietly-weaken-the-
        # safety-property-the-code-protects).
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, "VERSION"))  # a directory, not a file
        os.makedirs(os.path.join(broken, "tools"))
        dangling = os.path.join(broken, "tools", "bm_store.py")
        os.symlink(os.path.join(broken, "does-not-exist"), dangling)
        self._clear_env()
        os.environ["BROTHERMODE_ROOT"] = broken
        try:
            found = bc.find_checkout()
        except Exception as exc:  # noqa: BLE001 - the property under test
            self.fail("find_checkout() raised on a broken candidate: %r"
                     % exc)
        # Falls through past the broken candidate to a real one instead
        # of stopping there.
        self.assertIsNotNone(found)
        self.assertNotEqual(found, broken)

    def test_doctor_gets_a_usable_path_from_umbrella_checkout(self):
        # A real caller (cmd_doctor via the "doctor" subcommand) must be
        # able to use the corrected return value the same way it uses the
        # flat shape: checkout/tools/<adapter>.
        nested = os.path.join(self.tmp, "runtime", "hooks", "brothermode")
        self._touch(os.path.join(nested, "tools", "bm_cursor.py"))
        self._touch(os.path.join(nested, "tools", "bm_store.py"))
        adapter_src = os.path.join(HERE, "bm_cursor_hook.py")
        shutil.copy(adapter_src, os.path.join(nested, "tools",
                                              "bm_cursor_hook.py"))
        proc = _run(
            [os.path.join(HERE, "bm_cursor.py"), "doctor",
             "--checkout", nested,
             "--hooks", os.path.join(self.tmp, "no-such-hooks.json")],
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("no BrotherMode checkout found", proc.stdout)
        self.assertNotIn(
            os.path.join(nested, "tools", "tools", "bm_cursor_hook.py"),
            proc.stdout)


class TestCursorHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cursor-test-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        subprocess.run(["git", "init"], cwd=self.tmp, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Need one commit for worktree add from HEAD.
        with io.open(os.path.join(self.tmp, "README"), "w",
                     encoding="utf-8") as fh:
            fh.write("x\n")
        subprocess.run(["git", "add", "README"], cwd=self.tmp, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "init"],
            cwd=self.tmp, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_dispatch_claim_record_adopt(self):
        env = os.environ.copy()
        env["BROTHERMODE_ROOT"] = ROOT
        tool = os.path.join(HERE, "bm_cursor.py")
        d = _run(
            [tool, "dispatch",
             "--objective", "touch marker",
             "--write-scope", "marker.txt",
             "--done-check", "test -f marker.txt",
             "--actor", "fable",
             "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(d.returncode, 0, d.stderr)
        packet = json.loads(d.stdout)
        pid = packet["packet_id"]
        self.assertEqual(packet["state"], "queued")

        c = _run(
            [tool, "claim-next", "--project", self.tmp, "--actor", "cursor",
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(c.returncode, 0, c.stderr)
        claimed = json.loads(c.stdout)
        self.assertEqual(claimed["state"], "claimed")
        self.assertEqual(claimed["packet_id"], pid)

        # Executor does the work in the project root.
        with io.open(os.path.join(self.tmp, "marker.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("ok\n")
        done_path = os.path.join(self.tmp, "done.txt")
        with io.open(done_path, "w", encoding="utf-8") as fh:
            fh.write("done\n")

        r = _run(
            [tool, "record-result",
             "--packet-id", pid,
             "--worker-claim", "wrote marker",
             "--artifact", "marker.txt",
             "--done-output", done_path,
             "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        returned = json.loads(r.stdout)
        self.assertEqual(returned["state"], "returned")

        a = _run(
            [tool, "adopt", "--packet-id", pid, "--project", self.tmp,
             "--json"],
            cwd=self.tmp, env=env,
        )
        self.assertEqual(a.returncode, 0, a.stderr)
        adopted = json.loads(a.stdout)
        self.assertEqual(adopted["state"], "adopted")
        self.assertEqual(adopted["adoption"]["verdict"], "passed")

    def test_mailbox_worker_pending(self):
        worker = bc.CursorMailboxWorker(project=self.tmp, actor="fable")
        result = worker.run({
            "unit_id": "u1",
            "objective": "do a thing",
            "read_scope": [],
            "write_scope": ["a.py"],
            "done_check": "true",
            "risk_class": "normal",
        })
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["artifacts"])
        self.assertTrue(os.path.isfile(result["artifacts"][0]))

    def test_emit_rules_has_frontmatter(self):
        tool = os.path.join(HERE, "bm_cursor.py")
        dest_dir = os.path.join(self.tmp, ".cursor", "rules")
        os.makedirs(dest_dir)
        proc = _run(
            [tool, "emit-rules", "--project", self.tmp,
             "--checkout", ROOT, "--force"],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with io.open(os.path.join(dest_dir, "brothermode.mdc"),
                     encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("alwaysApply: true", text)
        self.assertIn("BrotherMode", text)


class TestCursorInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-cursor-install-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.cursor = os.path.join(self.home, ".cursor")
        os.makedirs(self.cursor)
        self.target = os.path.join(self.cursor, "brothermode")
        self.hooks = os.path.join(self.cursor, "hooks.json")

    def _env(self):
        env = os.environ.copy()
        env["HOME"] = self.home
        return env

    def test_install_and_uninstall(self):
        # Point defaults by explicit flags rather than relying on HOME for
        # expanduser inside already-imported modules: pass --target/--hooks.
        inst = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(inst.returncode, 0, inst.stderr + inst.stdout)
        self.assertTrue(os.path.isfile(
            os.path.join(self.target, "tools", "bm_cursor_hook.py")))
        self.assertTrue(os.path.isfile(self.hooks))
        with io.open(self.hooks, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        self.assertEqual(doc.get("version"), 1)
        self.assertIn("preToolUse", doc["hooks"])
        self.assertIn("beforeShellExecution", doc["hooks"])
        joined = json.dumps(doc)
        self.assertIn("bm_cursor_hook.py", joined)
        self.assertIn(self.target, joined)
        record = os.path.join(self.cursor, "brothermode-install.json")
        self.assertTrue(os.path.isfile(record))

        # Foreign hook must survive uninstall.
        doc["hooks"]["stop"] = doc["hooks"].get("stop", []) + [{
            "command": "echo foreign-hook",
            "timeout": 5,
        }]
        with io.open(self.hooks, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")

        un = _run(
            [os.path.join(SCRIPTS, "uninstall_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks,
             "--remove-files"],
            env=self._env(),
        )
        self.assertEqual(un.returncode, 0, un.stderr + un.stdout)
        self.assertFalse(os.path.isdir(self.target))
        with io.open(self.hooks, encoding="utf-8") as fh:
            left = json.loads(fh.read())
        stop = left.get("hooks", {}).get("stop", [])
        self.assertTrue(any("foreign-hook" in (e.get("command") or "")
                            for e in stop if isinstance(e, dict)))
        self.assertFalse(any("bm_cursor_hook.py" in (e.get("command") or "")
                             for entries in left.get("hooks", {}).values()
                             for e in (entries or [])
                             if isinstance(e, dict)))

    def test_refuse_overwrite_without_upgrade(self):
        first = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = _run(
            [os.path.join(SCRIPTS, "install_cursor.py"),
             "--target", self.target,
             "--hooks", self.hooks],
            env=self._env(),
        )
        self.assertEqual(second.returncode, 4, second.stderr)


class TestCursorDocsAndSkills(unittest.TestCase):
    def test_docs_page_exists(self):
        path = os.path.join(ROOT, "docs", "CURSOR-COMPAT.md")
        self.assertTrue(os.path.isfile(path))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("ADVISORY", text)
        self.assertIn("decision record", text)
        self.assertIn("install_cursor.py", text)
        self.assertNotIn("\u2014", text)  # no em dash
        self.assertNotIn("\u2013", text)  # no en dash

    def test_skills_exist(self):
        for name in ("cursor-dispatch", "cursor-execute"):
            path = os.path.join(ROOT, "skills", name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), path)
            with io.open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("bm_cursor.py", text)


class Night0912InstallCursor(unittest.TestCase):
    def test_invalid_project_hooks_json_refused(self):
        import importlib.util

        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(here, '../scripts/install_cursor.py')
        spec = importlib.util.spec_from_file_location('install_cursor', script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        source = os.path.dirname(os.path.dirname(os.path.abspath(mod.__file__)))
        old_looks = mod._install.looks_like_brothermode
        old_copy = mod._install.copy_tree
        old_emit = mod._bmc.cmd_emit_rules
        old_write = mod._bmc._write_json
        old_smoke = mod.smoke
        try:
            mod._install.looks_like_brothermode = (
                lambda p: os.path.realpath(p) == os.path.realpath(source))
            mod._install.copy_tree = lambda s, t, d: 0
            mod._bmc.cmd_emit_rules = lambda argv: 0
            mod._bmc._write_json = lambda p, d: None
            mod.smoke = lambda t, h: []

            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, 'target')
                hooks = os.path.join(tmp, 'hooks.json')
                project = os.path.join(tmp, 'project')
                os.makedirs(os.path.join(project, '.cursor'))
                project_hooks = os.path.join(project, '.cursor', 'hooks.json')
                original = '{ invalid json'
                with open(project_hooks, 'w', encoding='utf-8') as fh:
                    fh.write(original)

                code = mod.main(['--target', target, '--hooks', hooks,
                                 '--project', project])

                self.assertEqual(mod.EXIT_REFUSED, code)
                with open(project_hooks, encoding='utf-8') as fh:
                    self.assertEqual(original, fh.read())
        finally:
            mod._install.looks_like_brothermode = old_looks
            mod._install.copy_tree = old_copy
            mod._bmc.cmd_emit_rules = old_emit
            mod._bmc._write_json = old_write
            mod.smoke = old_smoke


class Night0912UninstallCursor(unittest.TestCase):
    def test_malformed_project_hooks_refuses(self):
        import contextlib
        import importlib.util

        root = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(root, '../scripts/uninstall_cursor.py')
        spec = importlib.util.spec_from_file_location('uc_night0912', src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        old_home = os.environ.get('HOME')
        with tempfile.TemporaryDirectory() as tmp:
            os.environ['HOME'] = tmp
            try:
                proj = os.path.join(tmp, 'proj')
                os.makedirs(os.path.join(proj, '.cursor'))
                with open(os.path.join(proj, '.cursor', 'hooks.json'), 'w') as fh:
                    fh.write('not json at all\n')

                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    try:
                        rc = mod.main(['--project', proj, '--dry-run'])
                    except Exception as exc:
                        self.fail('uncaught %s: %s' % (type(exc).__name__, exc))

                self.assertEqual(rc, mod.EXIT_REFUSED)
                self.assertIn('not valid JSON', err.getvalue())
            finally:
                if old_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = old_home


class Night0912BmCursor(unittest.TestCase):
    """Three findings from the night sweep against cmd_claim, cmd_doctor
    and cmd_cancel."""

    def test_claim_next_claims_oldest_not_lexicographically_first(self):
        # 8dfeb8be8452: packet ids are random, so the lexicographically
        # first inbox filename is not the oldest queued packet.
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, ".brothermode", "cursor-mailbox")
            inbox = os.path.join(base, "inbox")
            os.makedirs(inbox)
            older = {"packet_id": "cx-zzzzzzzzzzzz", "state": "queued",
                     "created_at": "2020-01-01T00:00:00Z",
                     "updated_at": "2020-01-01T00:00:00Z"}
            newer = {"packet_id": "cx-aaaaaaaaaaaa", "state": "queued",
                     "created_at": "2025-01-01T00:00:00Z",
                     "updated_at": "2025-01-01T00:00:00Z"}
            for p in (older, newer):
                with open(os.path.join(inbox, p["packet_id"] + ".json"),
                          "w") as f:
                    json.dump(p, f)
            bc.cmd_claim(["--next", "--project", root])
            claimed = os.listdir(os.path.join(base, "claimed"))
            self.assertEqual(claimed, ["cx-zzzzzzzzzzzz.json"])

    def test_claim_next_is_not_stopped_by_one_unreadable_packet(self):
        # Review fix: sorting by created_at reads every inbox packet, so one
        # corrupt file must sort last instead of crashing every claim.
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, ".brothermode", "cursor-mailbox")
            inbox = os.path.join(base, "inbox")
            os.makedirs(inbox)
            with open(os.path.join(inbox, "cx-000000000000.json"), "w") as f:
                f.write("{not json")
            good = {"packet_id": "cx-bbbbbbbbbbbb", "state": "queued",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z"}
            with open(os.path.join(inbox, "cx-bbbbbbbbbbbb.json"), "w") as f:
                json.dump(good, f)
            bc.cmd_claim(["--next", "--project", root])
            claimed = os.listdir(os.path.join(base, "claimed"))
            self.assertEqual(claimed, ["cx-bbbbbbbbbbbb.json"])

    def test_doctor_survives_non_object_hooks_json(self):
        # 5b8d50323450: a hooks.json holding a valid JSON list (not an
        # object) must report FAIL, not crash with AttributeError.
        with tempfile.TemporaryDirectory() as td:
            hooks = os.path.join(td, "hooks.json")
            with open(hooks, "w") as f:
                f.write("[]")
            try:
                rc = bc.cmd_doctor(["--hooks", hooks, "--checkout", td])
            except AttributeError as e:
                self.fail("AttributeError: %s" % e)
            self.assertEqual(rc, bc.EXIT_FAILED)

    def test_cancel_refuses_an_already_returned_packet(self):
        # 5ebe1cc8d626: cmd_cancel only refused an archived packet; a
        # packet already returned (outbox, not archive) was silently
        # overwritten with state "cancelled" instead of being refused.
        with tempfile.TemporaryDirectory() as root:
            base = os.path.join(root, ".brothermode", "cursor-mailbox")
            outbox = os.path.join(base, "outbox")
            os.makedirs(outbox)
            pid = "cx-123456789abc"
            packet = {"packet_id": pid, "state": "returned",
                      "created_at": "2020-01-01T00:00:00Z",
                      "updated_at": "2020-01-01T00:00:00Z",
                      "result": {"status": "returned"}}
            with open(os.path.join(outbox, pid + ".json"), "w") as f:
                json.dump(packet, f)
            rc = bc.cmd_cancel(["--packet-id", pid, "--project", root])
            self.assertEqual(rc, bc.EXIT_REFUSED)
            archive_file = os.path.join(base, "archive", pid + ".json")
            self.assertFalse(os.path.isfile(archive_file))


if __name__ == "__main__":
    unittest.main()
