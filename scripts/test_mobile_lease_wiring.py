#!/usr/bin/env python3
"""MB3.b: a mobile run holds the named simulator's device lease.

Reuses the deterministic fake xcodebuild/xcrun of test_mobile_workflow.py,
extended so the fake `xcodebuild test` reads the lease database named by
BM_DEVICE_LEASE_DB (a throwaway file) and records the rows it saw, and so
every fake tool call is logged. Simulated devices only; nothing here touches
a real simulator or the machine's own lease store.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "products" / "brothermode" / "tools"))
import bm_device_lease as L  # noqa: E402
import mobile_workflow as M  # noqa: E402
import test_mobile_ship_map as SM  # noqa: E402
import test_mobile_workflow as TW  # noqa: E402

SIM = TW.SIM
SEEN = r'''  if os.path.exists(os.environ.get('BM_DEVICE_LEASE_DB','')):
   import sqlite3;c=sqlite3.connect(os.environ['BM_DEVICE_LEASE_DB'])
   (root/'lease-seen.json').write_text(json.dumps([list(r) for r in c.execute('select device_id,state,owner,dirty_reason from device_leases')]));c.close()
  print('fixture test completed');sys.exit(0)'''
TOOL = TW.TOOL.replace("  print('fixture test completed');sys.exit(0)", SEEN, 1).replace(
    "args=sys.argv[1:]; ", "args=sys.argv[1:]; open(os.path.join(os.environ['FIXTURE_ROOT'],'tools.log'),'a').write(' '.join(args)+chr(10)); ", 1)
assert TOOL.count("lease-seen") == 1 and TOOL.count("tools.log") == 1


class MobileLeaseWiringTest(unittest.TestCase):
    setUp_workflow = TW.MobileWorkflowTests.setUp
    git = TW.MobileWorkflowTests.git
    make_app = TW.MobileWorkflowTests.make_app

    def setUp(self):
        self.setUp_workflow()
        for name in ("xcodebuild", "xcrun"):
            (self.bin / name).write_text(TOOL)
        self.db = str(self.root / "leases.sqlite3")
        env = patch.dict(os.environ, {"BM_DEVICE_LEASE_DB": self.db})
        env.start(); self.addCleanup(env.stop)
        self.profile_path.write_text(json.dumps(self.profile))

    def run_at(self, name):
        return M.run_workflow(self.repo, self.profile_path, self.reference, self.root / name)

    def row(self):
        with L.DeviceLeaseStore(self.db) as store:
            return store.get(SIM)

    def assert_no_data_before_tools(self, out, text=None):
        with self.assertRaises(M.Refusal) as raised:
            self.run_at(out)
        self.assertEqual(raised.exception.status, "NO-DATA", str(raised.exception))
        if text:
            self.assertIn(text, str(raised.exception))
        self.assertFalse((self.root / "tools.log").exists(), "a tool ran after the refusal")
        self.assertFalse((self.root / out).exists(), "the output folder was created")
        return raised.exception

    def test_a_run_holds_the_device_lease_while_it_works(self):
        report, receipt = self.run_at("run")
        self.assertEqual(report["technical_status"], "PASS", report.get("reason"))
        seen = json.loads((self.root / "lease-seen.json").read_text())
        self.assertEqual([r[:3] for r in seen], [[SIM, "leased", "mobile_workflow"]])
        row = self.row()
        self.assertEqual(row["state"], "dirty")
        self.assertTrue(row["dirty_reason"].startswith("pool-used:"), row["dirty_reason"])
        self.assertEqual(report["simulator_lease"]["state"], "HELD")
        self.assertEqual(report["simulator_lease"]["device_id"], SIM)
        self.assertEqual(json.loads(receipt.read_text())["simulator_lease"]["state"], "HELD")

    def test_a_second_run_on_a_used_device_rehabilitates_it(self):
        first, _ = self.run_at("run1")
        second, _ = self.run_at("run2")
        self.assertEqual(first["technical_status"], "PASS", first.get("reason"))
        self.assertEqual(second["technical_status"], "PASS", second.get("reason"))
        self.assertFalse(first["simulator_lease"]["warm_reused"])
        self.assertTrue(second["simulator_lease"]["warm_reused"])
        self.assertEqual(self.row()["state"], "dirty")

    def test_a_device_held_by_another_owner_is_no_data_before_any_tool_runs(self):
        with L.DeviceLeaseStore(self.db) as store:
            store.claim(SIM, "other-tool", "other-session", 3600)
        self.assert_no_data_before_tools("run", "already-leased")
        self.assertEqual(self.row()["owner"], "other-tool")

    def test_a_foreign_quarantine_is_not_cleared(self):
        with L.DeviceLeaseStore(self.db) as store:
            lease = store.claim(SIM, "other-tool", "other-session", 3600)
            store.mark_dirty(SIM, "erase failed on the bench", lease_uuid=lease["lease_uuid"])
        self.assert_no_data_before_tools("run", "clear-dirty")
        self.assertEqual(self.row()["dirty_reason"], "erase failed on the bench")

    def test_an_installed_plugin_without_the_lease_store_still_runs(self):
        with patch.dict(sys.modules, {"mobile_simulator_pool": None}):
            report, _ = self.run_at("run")
        self.assertEqual(report["technical_status"], "PASS", report.get("reason"))
        self.assertEqual(report["simulator_lease"]["state"], "NO-DATA")
        self.assertIn("per user native lease", report["simulator_lease"]["reason"])
        self.assertFalse(os.path.exists(self.db))

    def test_the_user_flock_still_refuses_a_second_brother_run(self):
        with M.lease():
            self.assert_no_data_before_tools("run", "native tool lease")

    def test_a_corrupt_lease_database_is_no_data(self):
        Path(self.db).write_bytes(b"this is not a sqlite database, only garbage bytes " * 40)
        self.assert_no_data_before_tools("run")

    def test_the_pool_has_a_production_caller_and_the_map_says_ship(self):
        callers = SM.production_callers(str(HERE), "mobile_simulator_pool")
        self.assertIn("scripts/mobile_workflow.py", callers)
        entry = {e["module"]: e for e in SM.load_entries()}["mobile_simulator_pool"]
        self.assertEqual(entry["class"], "SHIP")


if __name__ == "__main__":
    unittest.main()
