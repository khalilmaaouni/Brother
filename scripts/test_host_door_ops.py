#!/usr/bin/env python3
"""OUT1.l integrator cases beside the frozen HostHealthAccess contract (scripts/test_host_access_contract.py).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: each host
route (identity, update, diagnose, verify, repair) reaches a tool inside the INSTALLED plugin and reports that install,
never the checkout the door sits in; diagnosis never writes; repair without write authority starts nothing; a missing
install or tool gives one precise setup action and says how to resume; each host's enforcement boundary stays as
demonstrated (Cursor advisory, Codex not shown); acceptances are kept by brother_state's one rule; and REACH.json's
rows follow the door's real routes, with no installed_execution verdict that lacks its walk evidence.

Hermetic: every install is a temp folder, every tool a scripted fake, no HOME read, no network.

Run: python3 -B scripts/test_host_door_ops.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import door  # noqa: E402

REVISION = "a" * 40
JOURNEYS = os.path.join(HERE, "journey", "journeys.json")
#: The door's capability families, each with the run function REACH must name as the route's marker.
FAMILIES = {"vault.": ("VAULT_CAPABILITIES", "def run_vault_capability"),
            "data.": ("DATA_CAPABILITIES", "def run_data_capability"),
            "mobile.": ("MOBILE_CAPABILITIES", "def run_mobile_capability"),
            "assurance.": ("ASSURANCE_CAPABILITIES", "def run_assurance_capability"),
            "host.": ("HOST_CAPABILITIES", "def run_host_capability")}


def tool_script(exit_code, text, marker=None):
    """A scripted installed tool: prints `text`, writes `marker` when it is given --fix, exits `exit_code`."""
    body = "import sys\n"
    if marker:
        body += "if '--fix' in sys.argv:\n    open(%r, 'w').write('repaired')\n" % marker
    return body + "print(%r)\nsys.exit(%d)\n" % (text, exit_code)


class Install(object):
    """A scripted install recorded the way the host records one, under a temp config dir."""

    def __init__(self, tmp, doctor_exit=0, verify_exit=0, update_exit=0, deny=True, doctor=True):
        self.config = os.path.join(tmp, "config")
        self.root = os.path.join(self.config, "plugins", "cache", "brother", "brother", "9.9.9")
        self.marker = os.path.join(tmp, "repaired.txt")
        for d in ("runtime/hooks/brothermode/tools", "runtime/hooks/brothermode/scripts", ".claude-plugin", "hooks"):
            os.makedirs(os.path.join(self.root, d))
        self.write("runtime/RUNTIME-MANIFEST.json", json.dumps({"files": [], "source_revision": REVISION}))
        self.write(".claude-plugin/plugin.json", json.dumps({"name": "brother", "version": "9.9.9"}))
        self.write("hooks/hooks.json", json.dumps({"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "class": "deny" if deny else "advisory", "command": "true"}]}]}}))
        self.write("runtime/verify_runtime.py", tool_script(verify_exit, "verify_runtime: scripted"))
        self.write("runtime/hooks/brothermode/tools/brothermode_cli.py",
                   tool_script(update_exit, json.dumps({"status": "up-to-date"})))
        if doctor:
            self.write("runtime/hooks/brothermode/scripts/doctor.py",
                       tool_script(doctor_exit, json.dumps({"checks": []}), self.marker))
        self.write_record({"brother@brother": [{"installPath": self.root, "version": "9.9.9"}]})

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def write_record(self, plugins):
        with open(os.path.join(self.config, "plugins", "installed_plugins.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "plugins": plugins}, fh)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="host-door-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, capability, install, allow=None):
        return door.run_host_capability(capability, allow=allow, config=install.config)


class EachHostRoute(_Base):

    def test_the_table_names_the_five_routes_and_only_repair_writes(self):
        rows = {r["id"]: r for r in door.HOST_CAPABILITIES}
        self.assertEqual({"host.identity", "host.update", "host.diagnose", "host.verify", "host.repair"}, set(rows))
        self.assertEqual("host_existing_doctor", rows["host.diagnose"]["handler"])
        for cap, row in rows.items():
            self.assertEqual(["write"] if cap == "host.repair" else ["read"], row["required_authority"], cap)

    def test_identity_reads_the_installed_manifest(self):
        got = self.ask("host.identity", Install(self.tmp))
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertIn(REVISION, got["output"])
        self.assertIn("9.9.9", got["output"])

    def test_update_reports_the_installed_command_line_answer(self):
        self.assertEqual("COMPLETE", self.ask("host.update", Install(self.tmp))["state"])

    def test_an_update_check_that_could_not_reach_the_release_list_is_no_data(self):
        got = self.ask("host.update", Install(self.tmp, update_exit=1))
        self.assertEqual("NO-DATA", got["verdict"], got)

    def test_diagnose_passes_and_fails_with_the_doctor(self):
        self.assertEqual("COMPLETE", self.ask("host.diagnose", Install(self.tmp))["state"])
        other = tempfile.mkdtemp(prefix="host-door-b-", dir=self.tmp)
        self.assertEqual("FAILED", self.ask("host.diagnose", Install(other, doctor_exit=1))["state"])

    def test_verify_runs_the_installed_verifier(self):
        self.assertEqual("COMPLETE", self.ask("host.verify", Install(self.tmp))["state"])
        for code, want in ((1, "FAIL"), (2, "NO-DATA")):
            other = tempfile.mkdtemp(prefix="host-door-v-", dir=self.tmp)
            self.assertEqual(want, self.ask("host.verify", Install(other, verify_exit=code))["verdict"], code)

    def test_repair_with_write_runs_the_doctors_own_fix(self):
        install = Install(self.tmp)
        got = self.ask("host.repair", install, allow=["write"])
        self.assertEqual("COMPLETE", got["state"], got)
        self.assertTrue(os.path.isfile(install.marker), "repair never reached the doctor's --fix")

    def test_the_command_line_entry_point_routes_host(self):
        install = Install(self.tmp)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER", "BM_"))}
        env.update(CLAUDE_CONFIG_DIR=install.config, HOME=self.tmp)
        # the real command line entry in a fresh interpreter, with host.* admitted the way an installed journey row
        # admits it (OUT1.n); a bare checkout admits nothing, which the hidden case below covers
        def cli(capability):
            entry = ("import sys; sys.path.insert(0, %r); import door; "
                     "door.consumer_admission = lambda *a, **k: {'candidate': 't', 'host': 'claude', "
                     "'capabilities': ['host.*'], 'routes': set(), 'queue': []}; "
                     "sys.exit(door.main(['--capability', %r]))" % (HERE, capability))
            return subprocess.run([sys.executable, "-B", "-c", entry], env=env, capture_output=True, text=True,
                                  timeout=120)
        ok = cli("host.identity")
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)
        self.assertIn(REVISION, ok.stdout)
        refused = cli("host.repair")
        self.assertEqual(1, refused.returncode, refused.stdout + refused.stderr)
        self.assertFalse(os.path.isfile(install.marker))

    def test_the_command_line_hides_host_when_it_has_not_earned_access(self):
        install = Install(self.tmp)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER", "BM_"))}
        env.update(CLAUDE_CONFIG_DIR=install.config, HOME=self.tmp)
        entry = ("import sys; sys.path.insert(0, %r); import door; "
                 "door.consumer_admission = lambda *a, **k: {'candidate': 't', 'host': 'claude', "
                 "'capabilities': [], 'routes': set(), 'queue': []}; "
                 "sys.exit(door.main(['--capability', 'host.identity']))" % HERE)
        got = subprocess.run([sys.executable, "-B", "-c", entry], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(2, got.returncode, got.stdout + got.stderr)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(got.stdout)["output"])
        self.assertNotIn(REVISION, got.stdout)


class DiscoveryNeverMutates(_Base):

    def test_diagnose_never_writes(self):
        install = Install(self.tmp)
        calls = []

        def run(argv):
            calls.append(list(argv))
            return 0, "{}"
        got = door.run_host_capability("host.diagnose", config=install.config,
                                       invoke=lambda h, i: door.host_invoke(h, i, config=install.config, run=run))
        self.assertEqual("COMPLETE", got["state"])
        self.assertEqual(1, len(calls))
        self.assertNotIn("--fix", calls[0], "diagnosis asked the doctor to repair")
        self.ask("host.diagnose", install)
        self.assertFalse(os.path.isfile(install.marker), "diagnosis wrote through the installed doctor")

    def test_no_read_route_writes(self):
        install = Install(self.tmp)
        for cap in ("host.identity", "host.update", "host.diagnose", "host.verify"):
            self.ask(cap, install, allow=["write", "approve"])
        self.assertFalse(os.path.isfile(install.marker))

    def test_repair_without_write_refuses_and_starts_nothing(self):
        install = Install(self.tmp)
        calls = []
        got = door.run_host_capability("host.repair", config=install.config,
                                       invoke=lambda h, i: calls.append(h) or {"verdict": "PASS"})
        self.assertEqual("REFUSED", got["state"], got)
        self.assertEqual(["write"], got["missing_authority"])
        self.assertEqual([], calls)
        self.ask("host.repair", install)
        self.assertFalse(os.path.isfile(install.marker))


class InstalledNotCheckout(_Base):

    def checkout_revision(self):
        with open(os.path.join(ROOT, "bundle", "runtime", "RUNTIME-MANIFEST.json"), encoding="utf-8") as fh:
            return json.load(fh).get("source_revision")

    def test_identity_is_the_installs_even_from_a_checkout(self):
        self.assertNotEqual(REVISION, self.checkout_revision(), "the fixture must differ from the checkout")
        got = self.ask("host.identity", Install(self.tmp))
        self.assertIn(REVISION, got["output"])
        self.assertNotIn(self.checkout_revision(), got["output"])
        self.assertNotIn(os.path.join(ROOT, "bundle"), got["output"])

    def test_a_checkouts_bundle_is_never_taken_for_an_install(self):
        install = Install(self.tmp)
        root, _why = door.installed_plugin(install.config, here=os.path.join(ROOT, "bundle", "runtime"))
        self.assertEqual(install.root, root)

    def test_a_door_inside_an_install_reads_its_own_install(self):
        install = Install(self.tmp)
        root, _why = door.installed_plugin(os.path.join(self.tmp, "no-config"),
                                           here=os.path.join(install.root, "runtime"))
        self.assertEqual(install.root, root)


class MissingDependency(_Base):

    def test_no_install_gives_one_setup_action(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        got = door.run_host_capability("host.diagnose", config=empty)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertEqual(door.HOST_SETUP, got["setup_action"])
        self.assertIsInstance(got["setup_action"], str)
        self.assertIn("host.diagnose", got["resume"])

    def test_a_missing_tool_inside_an_install_gives_the_reinstall(self):
        got = self.ask("host.diagnose", Install(self.tmp, doctor=False))
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertEqual(door.HOST_REINSTALL, got["setup_action"])

    def test_a_working_route_carries_no_setup_action(self):
        self.assertNotIn("setup_action", self.ask("host.identity", Install(self.tmp)))


class EnforcementStaysVisible(_Base):

    def test_cursor_stays_advisory_and_codex_is_not_shown_enforced(self):
        got = self.ask("host.identity", Install(self.tmp))["enforcement"]
        self.assertTrue(got["cursor"].startswith("advisory"), got)
        self.assertTrue(got["codex"].startswith("NO-DATA"), got)
        for host in ("cursor", "codex"):
            self.assertNotIn("enforced:", got[host], host)
        self.assertTrue(got["claude"].startswith("enforced"), got)

    def test_claude_is_enforced_only_when_the_installed_hooks_deny(self):
        got = self.ask("host.identity", Install(self.tmp, deny=False))["enforcement"]
        self.assertTrue(got["claude"].startswith("NO-DATA"), got)


class AcceptancesFollowTheOneRule(_Base):

    def load_copy(self):
        """accept_delivery.py as an install carries it: in a folder named runtime, beside nothing of the checkout."""
        runtime = os.path.join(self.tmp, "plugin", "runtime")
        os.makedirs(runtime)
        shutil.copy(os.path.join(HERE, "accept_delivery.py"), runtime)
        spec = importlib.util.spec_from_file_location("accept_installed", os.path.join(runtime, "accept_delivery.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, runtime

    def test_an_install_keeps_acceptances_per_user_not_in_the_plugin(self):
        mod, runtime = self.load_copy()
        state = os.path.join(self.tmp, "state")
        with mock.patch.dict(os.environ, {"BROTHER_RUNS_ROOT": state}):
            where = mod.deliveries_dir()
        self.assertEqual(os.path.join(state, "docs", "deliveries"), where)
        self.assertFalse(where.startswith(os.path.dirname(runtime)), where)

    def test_a_checkout_keeps_its_own_docs_deliveries(self):
        import accept_delivery
        with mock.patch.dict(os.environ, {"BROTHER_RUNS_ROOT": os.path.join(self.tmp, "ignored")}):
            self.assertEqual(os.path.join(ROOT, "docs", "deliveries"), accept_delivery.deliveries_dir())


def registry():
    with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


class ReachFollowsTheDoor(unittest.TestCase):
    """REACH.json is regenerated by hand from the door's routes, so these cases are what keep it from going stale."""

    def problems(self, data):
        caps = {c["id"]: c for c in data["capabilities"]}
        bad = []
        for prefix, (table, marker) in FAMILIES.items():
            ids = [c["id"] for c in getattr(door, table)]
            routed = [c["id"] for c in caps.values() if c.get("command") == "scripts/door.py"
                      and c.get("marker") == marker]
            rows = [r for r in data["inventory"] if r.get("routed") in routed]
            if ids and not rows:
                bad.append("the door routes %s but no inventory row is routed to it" % prefix)
            for r in rows:
                named = re.findall(r"\b%s[a-z_]+" % re.escape(prefix), str(r.get("reason") or ""))
                if not any(n in ids for n in named):
                    bad.append("%s is routed through %s but names no door route of it" % (r["id"], prefix))
        with open(JOURNEYS, encoding="utf-8") as fh:
            journeys = json.load(fh)
        known = set(journeys)
        for r in data["inventory"]:
            if r.get("installed_execution") == "NO-DATA":
                continue
            ev = r.get("installed_evidence")
            if not isinstance(ev, dict) or ev.get("journey") not in known or not ev.get("rows") or \
                    not re.match(r"^[0-9a-f]{64}$", str(ev.get("identity"))):
                bad.append("%s reads %s with no walk evidence" % (r["id"], r.get("installed_execution")))
                continue
            declared = {row["id"] for row in journeys[ev["journey"]]["rows"]}
            if not set(ev["rows"]) <= declared:
                bad.append("%s cites rows %s does not declare" % (r["id"], ev["journey"]))
        return bad

    def test_the_shipped_registry_follows_the_door(self):
        self.assertEqual([], self.problems(registry()))

    def test_a_door_family_with_no_routed_row_is_refused(self):
        data = registry()
        for r in data["inventory"]:
            if r.get("routed") == "install-health":
                r["routed"] = None
        self.assertTrue(any("host." in p for p in self.problems(data)))

    def test_a_row_routed_with_no_door_route_is_refused(self):
        data = registry()
        row = next(r for r in data["inventory"] if r["id"] == "vault-conflicts")
        row["routed"] = "vault-notes"
        self.assertTrue(any("vault-conflicts" in p for p in self.problems(data)))

    def test_an_installed_verdict_without_walk_evidence_is_refused(self):
        data = registry()
        row = next(r for r in data["inventory"] if r["installed_execution"] == "NO-DATA")
        row["installed_execution"] = "PASS"
        self.assertTrue(any(row["id"] in p for p in self.problems(data)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
