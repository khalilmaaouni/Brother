#!/usr/bin/env python3
"""test_hook_guard.py: the double-fire guard fires a bundled hook exactly
once, and never zero times (docs/architecture/ADR-ONE-PLUGIN-HOOKS.md).

Every case drives the guard at its entry point (a subprocess, the way
Claude Code runs it) with CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR pointed
at throwaway registries, and counts how often the wrapped command ran. One
condition per fixture:

  a.  bundle alone: the command runs once, stdout and exit code pass through
  b.  old plugin enabled, installed, registering the SAME hook: zero runs
  b1. old plugin enabled and installed but its hooks.json LACKS this hook
      (this machine's real brothermode 3.4.5 shape, no clock guard): runs
  b2. same script under a different event: runs
  b3. enabled in settings but not installed: runs
  b4. installed but disabled: runs
  b5. installed without hooks.json: runs
  b6. install path gone: runs
  b7. a sibling name (brother vs brothermode) never matches: runs
  b8. product "brother" itself with its own root: runs (yield_on_self)
  p1. disabled ONLY in project settings while user says enabled: runs
  p2. enabled ONLY in project settings: zero runs
  p3. local settings win over shared project settings
  c.  corrupt settings, corrupt registry, missing config dir, a settings
      file nested past the recursion limit: runs
  d.  two enabled brother roots registering the hook: only the first fires
  e.  the committed bundle/hooks/hooks.json wraps every command
  x.  unrunnable command: exit 2 on PreToolUse and Stop, 1 elsewhere, reason
      on stderr
  m1. the old plugin's matcher is NARROWER than the bundle's (Edit|Write vs
      ...|Bash): runs, else Bash would get the fence zero times
  m2. the old group has no matcher, or "*" (fires on every tool): zero runs
  m3. the bundle's matcher is empty (every tool), the old one names tools:
      runs
  m4. an unknown matcher shape (a regex), even identical on both sides: runs
  m5. no --matcher argument at all: runs
  s1. managed settings disable an old plugin the user enables: runs
  s2. managed settings enable an old plugin the user disables: zero runs
  s3. a command with no .py token beside an old plugin: runs
  s4. enabledPlugins value 1 (truthy, not true): runs
  s5. the v1 registry shape (one dict, not a list, per key): zero runs
  s6. an empty installPath while cwd holds a matching hooks.json: runs
  s7. the script is the FIRST .py token: a bundle hook whose later argument
      ends .py is not the old plugin's hook of that name: runs
  i1. an old install scoped to ANOTHER project: runs
  i2. an old install scoped to THIS project (through a symlink): zero runs
  i3. an old install with local scope for another project: runs
  i4. an old install with an unknown scope: runs
  i5. a project scoped install without projectPath: runs
  n1. old matcher "bash" (tool names are case sensitive) vs "Bash": runs
  n4. old matcher a strict superset (Edit|Write|Bash vs Bash): zero runs
  n5. the first old group does not cover, a later one does: zero runs
  n6. old matcher "Foo\\|Bash" is a regex, not a list: runs
  n7. old matcher ".*" is a regex, never read as every tool: runs
  f6. the old hook runs the script with other arguments: runs; with the
      same arguments: zero runs
  a2. the same arguments in another order: runs
  a3. the old hook adds a second argument: runs
  a6. the old hook repeats an argument: runs
  a5. an install for /x/proj never counts for /x/project (a prefix is not
      the same project): runs

The OP1.a hardenings of 2026-10-03 (docs/plan/specs/OP1.md section 5.1),
one case each, M-OP1A-14 to M-OP1A-21:

  (a) a blocking event (PreToolUse, Stop) never yields, to an identical old
      hook or to a first sorting brother root: runs
  (b) an existing settings layer that cannot be read, or has the wrong
      shape, makes enabledPlugins unknown: runs
  (c) an old hook whose script is missing, or lies outside its root: runs
  (d) a same named plugin from another marketplace never counts: runs
  (g) a config or project dir that a group or other user can write, or that
      is missing, a file or not the user's: runs; a CLAUDE_PLUGIN_ROOT that is
      not the root the guard ships in: runs; the managed layer off macOS is
      unknown: runs
  (h) the old command is split as a shell splits it, so a quoted path with
      a space is one token, and an unparsable command runs

Since (a), every case that proves a yield drives EVENT (PostToolUse), the one
non blocking tool event; since (c), Registry.install writes every script the
old hooks name; since (g), the guard runs from <root>/runtime/hooks/ when a
case names a plugin root, the way an install runs it.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GUARD = os.path.join(HERE, "hook_guard.py")
sys.path.insert(0, HERE)
import hook_guard  # noqa: E402  (the in process cases)

SCRIPT = "bm_fence_hook.py"
FIRE = [sys.executable, "-c", "import sys; print('fired'); sys.exit(7)",
        "tools/" + SCRIPT]
WIDE = "Edit|Write|MultiEdit|NotebookEdit|Bash"
#: The event the yield cases drive: the guard never yields on a blocking one.
EVENT = "PostToolUse"


def _one_hook(event, matcher, command):
    group = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return {"hooks": {event: [group]}}
REAL_345 = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/bm_sessionstart.py\""}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/bm_hookchain.py\" stop"}]}],
    "PreToolUse": [
        {"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": [
            {"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/bm_fence_hook.py\""},
            {"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/vault_recall_hook.py\" check"}]},
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/tools/bm_bash_audit.py\" pre"}]}],
}}
#: REAL_345 with its PreToolUse groups registered under EVENT as well, so
#: the cases that prove a yield have the same hooks on a non blocking event.
OLD = {"hooks": dict(REAL_345["hooks"], **{EVENT: REAL_345["hooks"]["PreToolUse"]})}


def _write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(doc, str):
            fh.write(doc)
        else:
            json.dump(doc, fh)


def _write_scripts(root, hooks):
    """Every script the old hooks name (the first .py token of a command),
    written under `root`: the guard yields only to a script that exists. A
    command the shell cannot split names none."""
    for groups in hooks.get("hooks", {}).values():
        for group in groups:
            for handler in group.get("hooks", []):
                try:
                    argv = shlex.split(handler["command"])
                except ValueError:
                    continue
                for token in argv:
                    if token.endswith(".py"):
                        _write(os.path.join(root, token.replace(
                            "${CLAUDE_PLUGIN_ROOT}/", "")), "")
                        break


class Registry(object):
    """Throwaway CLAUDE_CONFIG_DIR (settings.json, plugins/installed_plugins.json)
    and CLAUDE_PROJECT_DIR (.claude/settings.json, .claude/settings.local.json),
    both owned by this user and writable by no one else, as the guard requires."""

    def __init__(self, tmp):
        self.cfg = os.path.join(tmp, "cfg")
        self.project = os.path.join(tmp, "project")
        os.makedirs(self.project)
        self.enabled = {}
        self.plugins = {}
        self.tmp = tmp
        self.flush()
        os.chmod(self.cfg, 0o700)
        os.chmod(self.project, 0o700)

    def install(self, key, hooks=OLD, enabled=True, tail=None, **extra):
        root = os.path.join(self.tmp, "roots", tail or key.replace("@", "-"))
        os.makedirs(root, exist_ok=True)
        if hooks is not None:
            _write(os.path.join(root, "hooks", "hooks.json"), hooks)
            if isinstance(hooks, dict):
                _write_scripts(root, hooks)
        self.plugins.setdefault(key, []).append(dict(extra, installPath=root))
        self.enabled[key] = enabled
        self.flush()
        return root

    def project_settings(self, enabled, local=False):
        name = "settings.local.json" if local else "settings.json"
        _write(os.path.join(self.project, ".claude", name),
               {"enabledPlugins": enabled})

    def flush(self):
        _write(os.path.join(self.cfg, "settings.json"),
               {"enabledPlugins": self.enabled})
        _write(os.path.join(self.cfg, "plugins", "installed_plugins.json"),
               {"version": 2, "plugins": self.plugins})


def run_guard(reg, product="brothermode", event=EVENT, plugin_root=None,
              command=FIRE, cfg=None, matcher=WIDE, managed=None,
              guard_at=None, platform="darwin"):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=cfg or reg.cfg,
               CLAUDE_PROJECT_DIR=reg.project)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    flag = [] if matcher is None else ["--matcher=" + matcher]
    # The guard trusts only the root it ships in, so with a plugin root it
    # runs from <root>/runtime/hooks/ as an install runs it (guard_at, the
    # directory it runs from, overrides that); without one, from scripts/.
    if guard_at is None:
        guard_at = (os.path.join(plugin_root, "runtime", "hooks")
                    if plugin_root else HERE)
    if guard_at != HERE:
        os.makedirs(guard_at, exist_ok=True)
        shutil.copy2(GUARD, os.path.join(guard_at, "hook_guard.py"))
    # The managed layer lives at a fixed system path; the entry point main()
    # is driven with MANAGED_SETTINGS pointed at a fixture (a missing file
    # when the case sets none, so this machine's real file never leaks in),
    # and the platform is the one that path belongs to unless a case says
    # otherwise, so the suite reads the same on every machine.
    boot = ("import sys; sys.path.insert(0, %r); import hook_guard as g; "
            "g.MANAGED_SETTINGS = %r; sys.platform = %r; sys.argv[0] = %r; "
            "sys.exit(g.main())"
            % (guard_at, managed or os.path.join(reg.tmp, "no-managed.json"),
               platform, os.path.join(guard_at, "hook_guard.py")))
    proc = subprocess.run([sys.executable, "-B", "-c", boot, product, event]
                          + flag + command,
                          input="{}", capture_output=True, text=True, env=env,
                          cwd=reg.tmp)
    return proc.returncode, proc.stdout.count("fired"), proc.stderr


RAN = (7, 1)
QUIET = (0, 0)


class GuardFiresOnce(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hook-guard-")
        self.reg = Registry(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_bundle_alone_runs_the_command_once_with_its_exit_code(self):
        code, fired, err = run_guard(self.reg)
        self.assertEqual((code, fired), RAN, err)

    def test_b_same_hook_registered_by_enabled_installed_old_plugin_is_quiet(self):
        self.reg.install("brothermode@brother")
        code, fired, err = run_guard(self.reg)
        self.assertEqual((code, fired, err), (0, 0, ""))

    def test_b1_a_hook_the_old_plugin_lacks_still_fires(self):
        """This machine's shape: brothermode 3.4.5 enabled and installed,
        its hooks.json has no bm_clock_guard on PreToolUse or Stop. Under
        the full key the guard trusts (brothermode@brother), and on EVENT,
        since a blocking event runs whatever the old plugin holds."""
        self.reg.install("brothermode@brother")
        clock = FIRE[:-1] + ["tools/bm_clock_guard.py"]
        self.assertEqual(run_guard(self.reg, event="PreToolUse", command=clock)[:2], RAN)
        self.assertEqual(run_guard(self.reg, event="Stop", command=clock)[:2], RAN)
        self.assertEqual(run_guard(self.reg, command=clock)[:2], RAN)
        # and the hooks it does register stay quiet, same registry
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_b2_same_script_under_another_event_runs(self):
        self.reg.install("brothermode@brother")
        for event in ("SessionStart", "Stop"):
            self.assertEqual(run_guard(self.reg, event=event)[:2], RAN, event)

    def test_b3_enabled_but_not_installed_runs(self):
        self.reg.enabled["brothermode@brother"] = True
        self.reg.flush()
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_b4_installed_but_disabled_runs(self):
        self.reg.install("brothermode@brother", enabled=False)
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_b5_installed_without_hooks_runs(self):
        self.reg.install("brothermode@brother", hooks=None)
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_b6_install_path_gone_runs(self):
        root = self.reg.install("brothermode@brother")
        shutil.rmtree(root)
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_b7_a_sibling_name_never_matches(self):
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg, product="brother")[:2], RAN)
        self.reg.install("brothersbe@brother")
        self.assertEqual(run_guard(self.reg, product="brothers")[:2], RAN)

    def test_b8_the_bundles_own_name_with_its_own_root_runs(self):
        root = self.reg.install("brother@brother")
        self.assertEqual(run_guard(self.reg, product="brother", plugin_root=root)[:2], RAN)

    def test_p1_disabled_only_in_project_settings_runs(self):
        self.reg.install("brothermode@brother")
        self.reg.project_settings({"brothermode@brother": False})
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_p2_enabled_only_in_project_settings_is_quiet(self):
        self.reg.install("brothermode@brother", enabled=False)
        self.reg.project_settings({"brothermode@brother": True})
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_p3_local_settings_win_over_shared_project_settings(self):
        self.reg.install("brothermode@brother", enabled=False)
        self.reg.project_settings({"brothermode@brother": True})
        self.reg.project_settings({"brothermode@brother": False}, local=True)
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        self.reg.project_settings({"brothermode@brother": False})
        self.reg.project_settings({"brothermode@brother": True}, local=True)
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_c_corrupt_settings_runs(self):
        self.reg.install("brothermode@brother")
        _write(os.path.join(self.reg.cfg, "settings.json"), "{not json")
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_c_corrupt_registry_runs(self):
        self.reg.install("brothermode@brother")
        _write(os.path.join(self.reg.cfg, "plugins", "installed_plugins.json"), "[]")
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_c_missing_config_dir_runs(self):
        self.assertEqual(run_guard(self.reg, cfg=os.path.join(self.tmp, "nowhere"))[:2], RAN)

    def test_c_settings_nested_past_the_recursion_limit_runs(self):
        self.reg.install("brothermode@brother")
        deep = "[" * 100000 + "]" * 100000
        _write(os.path.join(self.reg.cfg, "settings.json"), deep)
        code, fired, err = run_guard(self.reg)
        self.assertEqual((code, fired), RAN, err)
        self.assertNotIn("Traceback", err)

    def test_d_two_brother_roots_only_the_first_fires(self):
        first = self.reg.install("brother@brother", tail="a-first")
        second = self.reg.install("brother@brother", tail="b-second")
        self.assertEqual(run_guard(self.reg, plugin_root=first)[:2], RAN)
        self.assertEqual(run_guard(self.reg, plugin_root=second)[:2], QUIET)

    def test_d_a_root_the_registry_does_not_know_runs(self):
        self.reg.install("brother@brother", tail="a-first")
        dev = os.path.join(self.tmp, "dev-checkout")
        os.makedirs(dev)
        self.assertEqual(run_guard(self.reg, plugin_root=dev)[:2], RAN)

    def test_m1_old_matcher_narrower_than_the_bundles_runs(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, "Edit|Write", "python3 tools/" + SCRIPT))
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_m2_old_group_without_matcher_covers_every_tool_is_quiet(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, None, "python3 tools/" + SCRIPT))
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_m2_old_star_matcher_covers_every_tool_is_quiet(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, "*", "python3 tools/" + SCRIPT))
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_m3_bundle_matcher_every_tool_old_names_tools_runs(self):
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg, matcher="")[:2], RAN)

    def test_m4_unknown_matcher_shape_runs_even_when_identical(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, "mcp__.*", "python3 tools/" + SCRIPT))
        self.assertEqual(run_guard(self.reg, matcher="mcp__.*")[:2], RAN)

    def test_m5_missing_matcher_argument_runs(self):
        # the old group fires on every tool, so only an absent matcher read
        # as unknown (never as "every tool") keeps this running
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, None, "python3 tools/" + SCRIPT))
        self.assertEqual(run_guard(self.reg, matcher=None)[:2], RAN)

    def test_s1_managed_settings_disable_wins_over_user_enable(self):
        self.reg.install("brothermode@brother")
        managed = os.path.join(self.tmp, "managed.json")
        _write(managed, {"enabledPlugins": {"brothermode@brother": False}})
        self.assertEqual(run_guard(self.reg, managed=managed)[:2], RAN)

    def test_s2_managed_settings_enable_wins_over_user_disable(self):
        self.reg.install("brothermode@brother", enabled=False)
        managed = os.path.join(self.tmp, "managed.json")
        _write(managed, {"enabledPlugins": {"brothermode@brother": True}})
        self.assertEqual(run_guard(self.reg, managed=managed)[:2], QUIET)

    def test_s3_a_command_with_no_script_never_yields(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, "sh tools/fence.sh"))
        no_py = FIRE[:-1] + ["tools/fence.sh"]
        self.assertEqual(run_guard(self.reg, command=no_py)[:2], RAN)

    def test_s4_a_truthy_enabled_value_is_not_true(self):
        self.reg.install("brothermode@brother")
        self.reg.enabled["brothermode@brother"] = 1
        self.reg.flush()
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_s5_v1_registry_dict_entry_is_read(self):
        root = self.reg.install("brothermode@brother")
        self.reg.plugins["brothermode@brother"] = {"installPath": root}
        self.reg.flush()
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_s6_empty_install_path_is_not_the_cwd(self):
        _write(os.path.join(self.tmp, "hooks", "hooks.json"), REAL_345)
        self.reg.plugins["brothermode@brother"] = [{"installPath": ""}]
        self.reg.enabled["brothermode@brother"] = True
        self.reg.flush()
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_s7_the_script_is_the_first_py_token(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, "python3 extra/settings_loader.py"))
        two = FIRE + ["extra/settings_loader.py"]
        self.assertEqual(run_guard(self.reg, command=two)[:2], RAN)

    def test_i1_install_scoped_to_another_project_runs(self):
        self.reg.install("brothermode@brother", scope="project",
                         projectPath=os.path.join(self.tmp, "other-project"))
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_i2_install_scoped_to_this_project_is_quiet(self):
        link = os.path.join(self.tmp, "project-link")
        os.symlink(self.reg.project, link)
        self.reg.install("brothermode@brother", scope="project", projectPath=link)
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_i3_local_install_for_another_project_runs(self):
        self.reg.install("brothermode@brother", scope="local",
                         projectPath=os.path.join(self.tmp, "other-project"))
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_i4_unknown_scope_runs(self):
        self.reg.install("brothermode@brother", scope="team",
                         projectPath=self.reg.project)
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_i5_project_scope_without_project_path_runs(self):
        self.reg.install("brothermode@brother", scope="project")
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def _old_matcher(self, matcher):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, matcher, "python3 tools/" + SCRIPT))

    def test_n1_tool_names_are_case_sensitive(self):
        self._old_matcher("bash")
        self.assertEqual(run_guard(self.reg, matcher="Bash")[:2], RAN)

    def test_n4_a_strict_superset_covers(self):
        self._old_matcher("Edit|Write|Bash")
        self.assertEqual(run_guard(self.reg, matcher="Bash")[:2], QUIET)

    def test_n5_a_later_group_can_cover(self):
        hooks = _one_hook(EVENT, "Edit", "python3 tools/" + SCRIPT)
        hooks["hooks"][EVENT].append(
            _one_hook(EVENT, WIDE, "python3 tools/" + SCRIPT)
            ["hooks"][EVENT][0])
        self.reg.install("brothermode@brother", hooks=hooks)
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_n6_an_escaped_bar_is_a_regex_not_a_list(self):
        self._old_matcher("Foo\\|Bash")
        self.assertEqual(run_guard(self.reg, matcher="Bash")[:2], RAN)

    def test_n7_dot_star_is_a_regex_not_every_tool(self):
        self._old_matcher(".*")
        self.assertEqual(run_guard(self.reg, matcher="Bash")[:2], RAN)

    def test_f6_other_arguments_run_same_arguments_are_quiet(self):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, "python3 tools/%s --audit-only" % SCRIPT))
        self.assertEqual(run_guard(self.reg, command=FIRE + ["check"])[:2], RAN)
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        self.assertEqual(
            run_guard(self.reg, command=FIRE + ["--audit-only"])[:2], QUIET)

    def _old_args(self, args):
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, "python3 tools/%s %s" % (SCRIPT, args)))

    def test_a2_argument_order_matters(self):
        self._old_args("b a")
        self.assertEqual(run_guard(self.reg, command=FIRE + ["a", "b"])[:2], RAN)

    def test_a3_an_extra_second_argument_is_another_hook(self):
        self._old_args("stop --audit-only")
        self.assertEqual(run_guard(self.reg, command=FIRE + ["stop"])[:2], RAN)

    def test_a6_a_repeated_argument_is_another_hook(self):
        self._old_args("a a")
        self.assertEqual(run_guard(self.reg, command=FIRE + ["a"])[:2], RAN)

    def test_a5_a_project_path_prefix_is_not_this_project(self):
        self.reg.install("brothermode@brother", scope="project",
                         projectPath=self.reg.project[:-3])
        self.assertEqual(run_guard(self.reg)[:2], RAN)

    def test_usage_error_without_a_command(self):
        proc = subprocess.run([sys.executable, "-B", GUARD, "brothermode", "Stop"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)

    def test_usage_error_with_a_matcher_and_no_command(self):
        proc = subprocess.run([sys.executable, "-B", GUARD, "brothermode", "Stop",
                               "--matcher="], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no command", proc.stderr)

    def test_x_unrunnable_blocking_hook_exits_2_with_the_reason(self):
        for event in ("PreToolUse", "Stop"):
            code, _, err = run_guard(self.reg, event=event,
                                     command=["/nonexistent/brother-hook.py"])
            self.assertEqual(code, 2, event)
            self.assertIn("cannot run", err)
            self.assertIn(event, err)

    def test_x_unrunnable_non_blocking_hook_exits_1(self):
        code, _, err = run_guard(self.reg, event="SessionStart",
                                 command=["/nonexistent/brother-hook.py"])
        self.assertEqual(code, 1)
        self.assertIn("cannot run", err)

    # The OP1.a hardenings (docs/plan/specs/OP1.md section 5.1). Each case
    # first shows its fixture yields, then breaks the one condition it names.

    def _fresh(self):
        """Another throwaway registry inside this case's temp dir."""
        return Registry(tempfile.mkdtemp(dir=self.tmp))

    def test_a_blocking_event_never_yields(self):
        """(a) M-OP1A-14: an identical old hook on PreToolUse or Stop, and a
        first sorting brother root on PreToolUse, still run."""
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        self.assertEqual(run_guard(self.reg, event="PreToolUse")[:2], RAN)
        stop = FIRE[:-1] + ["tools/bm_hookchain.py", "stop"]
        self.assertEqual(run_guard(self.reg, event="Stop", matcher="",
                                   command=stop)[:2], RAN)
        reg = self._fresh()
        reg.install("brother@brother", tail="a-first")
        second = reg.install("brother@brother", tail="b-second")
        self.assertEqual(run_guard(reg, product="brother", plugin_root=second)[:2], QUIET)
        self.assertEqual(run_guard(reg, product="brother", plugin_root=second,
                                   event="PreToolUse")[:2], RAN)

    def test_an_unreadable_existing_settings_layer_means_run(self):
        """(b) M-OP1A-15: the user layer enables the old plugin; a project,
        local or managed layer that exists but cannot be read or parsed, is
        not a JSON object, or carries an enabledPlugins that is not an
        object makes the answer unknown. An absent layer adds nothing."""
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        project = os.path.join(self.reg.project, ".claude", "settings.json")
        for bad in ("{not json", "[]", '"on"', '{"enabledPlugins": []}',
                    '{"enabledPlugins": null}', '{"enabledPlugins": "all"}'):
            _write(project, bad)
            self.assertEqual(run_guard(self.reg)[:2], RAN, bad)
        _write(project, {"theme": "dark"})
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        os.remove(project)
        os.symlink(os.path.join(self.tmp, "gone.json"), project)
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        os.remove(project)
        local = os.path.join(self.reg.project, ".claude", "settings.local.json")
        os.makedirs(local)
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        os.rmdir(local)
        managed = os.path.join(self.tmp, "managed.json")
        _write(managed, "{not json")
        self.assertEqual(run_guard(self.reg, managed=managed)[:2], RAN)
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_a_registered_but_missing_legacy_script_means_run(self):
        """(c) M-OP1A-16: the old hooks.json registers the same hook, but the
        script it names is missing, is a directory, or lies outside the old
        root (an absolute path, or a climb out of ${CLAUDE_PLUGIN_ROOT})."""
        root = self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        os.remove(os.path.join(root, "tools", SCRIPT))
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        os.makedirs(os.path.join(root, "tools", SCRIPT))
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        outside = os.path.join(self.tmp, "elsewhere", "tools", SCRIPT)
        _write(outside, "")
        for named in (outside,
                      "${CLAUDE_PLUGIN_ROOT}/../../../elsewhere/tools/" + SCRIPT):
            reg = self._fresh()
            reg.install("brothermode@brother", hooks=_one_hook(
                EVENT, WIDE, 'python3 "%s"' % named))
            self.assertEqual(run_guard(reg)[:2], RAN, named)

    def test_a_same_named_plugin_from_another_marketplace_never_counts(self):
        """(d) M-OP1A-17: brothermode@other, enabled, installed and
        registering the same hook, is not the old install, and neither is
        brothermode@brother-launch; the second root rule reads the full key
        too. Only <name>@brother counts."""
        self.reg.install("brothermode@other")
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        self.reg.install("brothermode@brother-launch")
        self.assertEqual(run_guard(self.reg)[:2], RAN)
        reg = self._fresh()
        reg.install("brother@other", tail="a-first")
        second = reg.install("brother@brother", tail="b-second")
        self.assertEqual(run_guard(reg, product="brother", plugin_root=second)[:2], RAN)
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)

    def test_a_steered_config_dir_means_run(self):
        """(g) M-OP1A-18: CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR are read,
        but a directory a group or other user can write, a missing one, a
        file, or one another user owns is not trusted, and neither are the
        defaults (~/.claude, the working directory) in that state."""
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        for path in (self.reg.cfg, self.reg.project):
            for mode in (0o777, 0o770, 0o702):
                os.chmod(path, mode)
                self.assertEqual(run_guard(self.reg)[:2], RAN, (path, oct(mode)))
            os.chmod(path, 0o700)
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        a_file = os.path.join(self.tmp, "a-file")
        _write(a_file, "")
        for bad in (os.path.join(self.tmp, "nowhere"), a_file):
            self.assertIsNone(hook_guard.config_dir({"CLAUDE_CONFIG_DIR": bad}))
            self.assertIsNone(hook_guard.project_dir({"CLAUDE_PROJECT_DIR": bad}))
        if os.getuid() != 0:  # "/" belongs to another user
            self.assertIsNone(hook_guard.config_dir({"CLAUDE_CONFIG_DIR": "/"}))
            self.assertIsNone(hook_guard.project_dir({"CLAUDE_PROJECT_DIR": "/"}))
        self.assertEqual(hook_guard.config_dir({"CLAUDE_CONFIG_DIR": self.reg.cfg}),
                         self.reg.cfg)
        self.assertEqual(hook_guard.project_dir({"CLAUDE_PROJECT_DIR": self.reg.project}),
                         self.reg.project)
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, ".claude"))
        os.chmod(os.path.join(home, ".claude"), 0o700)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(hook_guard.config_dir({}), os.path.join(home, ".claude"))
            os.chmod(os.path.join(home, ".claude"), 0o777)
            self.assertIsNone(hook_guard.config_dir({}))
        cwd = os.getcwd()
        try:
            os.chdir(self.reg.project)
            self.assertEqual(os.path.realpath(hook_guard.project_dir({})),
                             os.path.realpath(self.reg.project))
            os.chmod(self.reg.project, 0o777)
            self.assertIsNone(hook_guard.project_dir({}))
        finally:
            os.chdir(cwd)
            os.chmod(self.reg.project, 0o700)

    def test_a_quoted_legacy_script_path_with_a_space_is_compared_as_one_token(self):
        """(h) M-OP1A-19: the old command is split as a shell splits it, so a
        quoted path or argument with a space is one token, a quoted
        "old bm_fence_hook.py" is a script of another name, and a command
        the shell cannot split runs."""
        self.reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, 'python3 "${CLAUDE_PLUGIN_ROOT}/my tools/%s" "a b"' % SCRIPT))
        self.assertEqual(run_guard(self.reg, command=FIRE + ["a b"])[:2], QUIET)
        self.assertEqual(run_guard(self.reg, command=FIRE + ["a", "b"])[:2], RAN)
        reg = self._fresh()
        root = reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/old %s"' % SCRIPT))
        _write(os.path.join(root, SCRIPT), "")
        self.assertEqual(run_guard(reg)[:2], RAN)
        reg = self._fresh()
        root = reg.install("brothermode@brother", hooks=_one_hook(
            EVENT, WIDE, 'python3 "tools/%s' % SCRIPT))
        _write(os.path.join(root, "tools", SCRIPT), "")
        self.assertEqual(run_guard(reg)[:2], RAN)

    def test_a_foreign_plugin_root_in_the_environment_means_run(self):
        """(g) M-OP1A-20: the guard's own root is read from its own path
        (<root>/runtime/hooks/hook_guard.py). A CLAUDE_PLUGIN_ROOT naming
        the second root, told to a guard that ships in the first root, in a
        checkout no registry knows, or in scripts/, decides no yield."""
        first = self.reg.install("brother@brother", tail="a-first")
        second = self.reg.install("brother@brother", tail="b-second")
        self.assertEqual(run_guard(self.reg, plugin_root=second)[:2], QUIET)
        dev = os.path.join(self.tmp, "dev-checkout", "runtime", "hooks")
        for guard_at in (os.path.join(first, "runtime", "hooks"), dev, HERE):
            self.assertEqual(run_guard(self.reg, plugin_root=second,
                                       guard_at=guard_at)[:2], RAN, guard_at)
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        self.assertEqual(run_guard(self.reg, plugin_root=second, guard_at=dev)[:2], RAN)

    def test_an_unknown_managed_settings_platform_means_run(self):
        """(g) M-OP1A-21: the managed layer is read only from
        MANAGED_SETTINGS, the macOS path; on any other platform it is
        unknown, never absent, unless the caller names its path."""
        self.reg.install("brothermode@brother")
        self.assertEqual(run_guard(self.reg)[:2], QUIET)
        for platform in ("linux", "win32"):
            self.assertEqual(run_guard(self.reg, platform=platform)[:2], RAN, platform)
        missing = os.path.join(self.tmp, "no-managed.json")
        cfg, project = self.reg.cfg, self.reg.project
        with mock.patch.object(hook_guard, "MANAGED_SETTINGS", missing):
            with mock.patch.object(sys, "platform", "linux"):
                self.assertIsNone(hook_guard.effective_enabled(cfg, project))
                self.assertEqual(hook_guard.effective_enabled(cfg, project, missing),
                                 {"brothermode@brother": True})
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(hook_guard.effective_enabled(cfg, project),
                                 {"brothermode@brother": True})

    def test_hostile_arguments_and_shapes_mean_run_never_a_crash(self):
        """None, a number, NaN, a bool, bytes, a str where a list or tuple
        belongs and unhashable values, handed to each argument of the
        guard's functions, and hostile shapes in the old hooks.json and the
        install registry, are the RUN answer: no exception, no yield."""
        root = self.reg.install("brothermode@brother")
        hook = (SCRIPT, ())
        cfg, project = self.reg.cfg, self.reg.project
        hostile = (None, 7, float("nan"), True, b"x", [], ["x"], {}, {"a": 1})
        missing = os.path.join(self.tmp, "no-managed.json")
        with mock.patch.object(hook_guard, "MANAGED_SETTINGS", missing), \
                mock.patch.object(sys, "platform", "darwin"):
            good = ["brothermode", EVENT, WIDE, FIRE, None, cfg, project]
            self.assertTrue(hook_guard.should_yield(*good))
            roots = hook_guard.active_hook_roots("brothermode", EVENT, WIDE, hook,
                                                 cfg, project)
            self.assertEqual(roots, [os.path.realpath(root)])
            self.assertTrue(hook_guard.registers_same_hook(root, EVENT, WIDE, hook))
            for bad in hostile:
                for i in range(len(good)):
                    if i == 4 and bad is None:
                        continue  # no plugin root is the good value
                    args = list(good)
                    args[i] = bad
                    self.assertFalse(hook_guard.should_yield(*args), (i, bad))
                for i, value in enumerate(("brothermode", EVENT, WIDE, hook, cfg, project)):
                    args = ["brothermode", EVENT, WIDE, hook, cfg, project]
                    args[i] = bad
                    self.assertEqual(hook_guard.active_hook_roots(*args), [], (i, bad))
                for i in range(4):
                    args = [root, EVENT, WIDE, hook]
                    args[i] = bad
                    self.assertFalse(hook_guard.registers_same_hook(*args), (i, bad))
                self.assertIsNone(hook_guard.effective_enabled(bad, project, missing))
                self.assertIsNone(hook_guard.effective_enabled(cfg, bad, missing))
                if bad is not None:
                    self.assertIsNone(hook_guard.effective_enabled(cfg, project, bad))
                self.assertFalse(hook_guard.trusted_dir(bad))
                if bad:  # a falsy value is an unset one: the default applies
                    self.assertIsNone(hook_guard.config_dir({"CLAUDE_CONFIG_DIR": bad}))
                    self.assertIsNone(hook_guard.project_dir({"CLAUDE_PROJECT_DIR": bad}))
                if bad is not None and not isinstance(bad, dict):
                    self.assertIsNone(hook_guard.config_dir(bad))
                    self.assertIsNone(hook_guard.project_dir(bad))
            # a str where a list or a tuple belongs
            self.assertFalse(hook_guard.should_yield(
                "brothermode", EVENT, WIDE, "python3 tools/" + SCRIPT, None, cfg, project))
            self.assertFalse(hook_guard.registers_same_hook(root, EVENT, WIDE, SCRIPT))
            self.assertEqual(hook_guard.active_hook_roots(
                "brothermode", EVENT, WIDE, SCRIPT, cfg, project), [])
            reg = self._fresh()
            odd = reg.install("brothermode@brother", hooks=None)
            _write(os.path.join(odd, "hooks", "hooks.json"), {"hooks": {EVENT: [
                "x", None, 7,
                {"matcher": 7, "hooks": [{"command": "python3 tools/" + SCRIPT}]},
                {"matcher": WIDE, "hooks": "python3 tools/" + SCRIPT},
                {"matcher": WIDE, "hooks": [None, "x", {"command": 7},
                                            {"command": ["python3", "tools/" + SCRIPT]}]}]}})
            _write(os.path.join(odd, "tools", SCRIPT), "")
            self.assertFalse(hook_guard.registers_same_hook(odd, EVENT, WIDE, hook))
            reg.plugins["brothermode@brother"] = [
                7, None, "x", {"installPath": 7}, {"installPath": ["x"]},
                {"installPath": root, "scope": 7}, {"installPath": root, "scope": []}]
            reg.enabled.update({"brothermode@other": True})
            reg.flush()
            self.assertEqual(hook_guard.active_hook_roots(
                "brothermode", EVENT, WIDE, hook, reg.cfg, reg.project), [])
            self.assertEqual(run_guard(reg)[:2], RAN)


class TheCommittedBundleWrapsEveryCommand(unittest.TestCase):

    def test_e_every_hooks_json_command_runs_through_the_guard_with_its_event(self):
        path = os.path.join(REPO, "bundle", "hooks", "hooks.json")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        prefix = 'python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" '
        seen = 0
        for event, groups in doc["hooks"].items():
            for g in groups:
                for h in g["hooks"]:
                    seen += 1
                    self.assertTrue(h["command"].startswith(prefix), h["command"])
                    product, ev, flag = h["command"][len(prefix):].split(" ", 3)[:3]
                    self.assertIn(product, ("brothermode", "brothersbe"))
                    self.assertEqual(ev, event, h["command"])
                    self.assertEqual(flag, '"--matcher=%s"' % g.get("matcher", ""),
                                     h["command"])
        self.assertGreater(seen, 0)
        self.assertFalse(os.path.exists(os.path.join(REPO, "bundle", "hooks", "union.json")))

    def test_e_shipped_guard_is_the_source(self):
        shipped = os.path.join(REPO, "bundle", "runtime", "hooks", "hook_guard.py")
        with open(shipped, "rb") as fh, open(GUARD, "rb") as gh:
            self.assertEqual(fh.read(), gh.read())


if __name__ == "__main__":
    unittest.main()
