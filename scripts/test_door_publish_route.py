#!/usr/bin/env python3
"""KEY1.d: publishing is hidden from consumers until it ships.

A publish ask is routed to the cut driver only from a maintainer checkout where scripts/cut_driver.py exists, through
OUT1.n's admission decision. On an installed plugin it gets the short truthful limitation and a supported next step,
and never a command naming a file the install does not contain. Everything the detection cannot decide reads as
installed, the safe side.

Both layouts are driven through the production entry point, `door.py --route`, by subprocess with a clean environment:
the checkout this file lives in (the maintainer layout) and a copy of bundle/ under a fake client plugin cache (the
installed layout the OUT1.n tests use). The detection edges and the admission tier guard are unit cases on the pure
functions.

Hermetic: temp trees only, no HOME read, no network, no model.

Run: python3 -B scripts/test_door_publish_route.py
"""
import json
import os
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

#: every phrasing of a publish ask the journey walkers and the registry paraphrases use
PUBLISH_ASKS = ("publish the release", "publish it", "publish the checked result now", "is the release ready to cut",
                "ship the release", "cut version 1.2.3 now", "release it")
#: a reply to a consumer may name none of these: the two cut files and the checkout's scripts folder
FORBIDDEN = ("cut_driver", "cut.py", "scripts/", "cut-release")


def run_door(path, *args, cwd=None):
    """(rc, stdout, stderr) of one door command line with no BROTHER_* or CLAUDE_* variable inherited."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "CLAUDE_"))}
    got = subprocess.run([sys.executable, "-I", "-B", path] + list(args), cwd=cwd or os.path.dirname(path), env=clean,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    return got.returncode, got.stdout, got.stderr


class InstalledHidesCut(unittest.TestCase):
    """The installed layout: bundle/ copied under a client plugin cache, its runtime/door.py run as a consumer runs it.
    The shipped journey records are re-hosted so an ordinary route is admitted on this machine, which proves the cut is
    hidden on its own and not because everything is."""
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="key1d-bundle-")
        cls.bundle = os.path.join(cls.dir, ".claude", "plugins", "cache", "brother", "brother", "9.9.9")
        shutil.copytree(os.path.join(ROOT, "bundle"), cls.bundle)
        cls.door = os.path.join(cls.bundle, "runtime", "door.py")
        for jid in ("J1", "J2", "J4"):
            path = os.path.join(cls.bundle, "runtime", "data", "JOURNEY-%s.json" % jid)
            with open(path) as fh:
                doc = json.load(fh)
            doc["host"] = door._os_host()
            with open(path, "w") as fh:
                json.dump(doc, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_install_carries_no_cut_file(self):
        """The premise: neither cut file ships, flat beside the door or anywhere under the bundle."""
        shipped = {name for _, _, names in os.walk(self.bundle) for name in names}
        self.assertFalse(shipped & {door.CUT_DRIVER, "cut.py"}, shipped & {door.CUT_DRIVER, "cut.py"})

    def test_an_ordinary_route_is_admitted_here(self):
        rc, out, err = run_door(self.door, "--route", "build me a login screen", cwd=self.dir)
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("route: build-outcome", out)

    def test_every_publish_ask_gets_the_limitation_and_names_no_absent_file(self):
        for ask in PUBLISH_ASKS:
            rc, out, err = run_door(self.door, "--route", ask, cwd=self.dir)
            self.assertEqual(rc, 2, (ask, out, err))
            self.assertIn(door.PUBLISH_LIMIT, out, ask)
            self.assertIn("(route: none)", out, ask)
            for token in FORBIDDEN:
                self.assertNotIn(token, out + err, (ask, token))

    def test_the_limitation_names_a_supported_next_step(self):
        self.assertIn("wrap up and close the session", door.PUBLISH_LIMIT)
        self.assertIn("nothing was started", door.PUBLISH_LIMIT)

    def test_every_consumer_line_the_publish_route_can_print_names_no_absent_file(self):
        """The strings the route prints to a consumer, read from the installed door's own copy, name no cut file."""
        with open(self.door, encoding="utf-8") as fh:
            installed = fh.read()
        self.assertIn(door.PUBLISH_LIMIT.split(":")[0], installed)
        for line in (door.PUBLISH_LIMIT, door.ADMISSION_LIMIT, door.NODATA + ": " + door.PUBLISH_LIMIT + " (route: none)"):
            for token in FORBIDDEN:
                self.assertNotIn(token, line, (line, token))

    def test_a_cut_driver_copied_flat_beside_the_installed_door_still_hides(self):
        """cut_driver.py present but not a maintainer checkout: the folder is runtime/, not scripts/, and no .git sits
        beside it, so the ask still reads installed."""
        stray = os.path.join(self.bundle, "runtime", door.CUT_DRIVER)
        shutil.copy(os.path.join(ROOT, "scripts", door.CUT_DRIVER), stray)
        self.addCleanup(lambda: os.path.exists(stray) and os.remove(stray))
        rc, out, err = run_door(self.door, "--route", "publish the release", cwd=self.dir)
        self.assertEqual(rc, 2, (out, err))
        self.assertIn(door.PUBLISH_LIMIT, out)
        for token in FORBIDDEN:
            self.assertNotIn(token, out + err, token)

    def test_the_available_surface_lists_no_cut_route(self):
        rc, out, err = run_door(self.door, "--available", cwd=self.dir)
        self.assertEqual(rc, 0, (out, err))
        self.assertNotIn(door.PUBLISH_ROUTE, json.loads(out)["routes"])


class MaintainerRoutesCut(unittest.TestCase):
    """The maintainer layout is this checkout: scripts/door.py beside scripts/cut_driver.py with .git beside them."""
    door_path = os.path.join(HERE, "door.py")

    def test_the_checkout_is_a_maintainer_checkout(self):
        self.assertTrue(os.path.isfile(os.path.join(HERE, door.CUT_DRIVER)))
        self.assertTrue(door.maintainer_checkout(HERE))

    def test_a_publish_ask_is_routed_to_the_cut_driver(self):
        for ask in ("publish the release", "publish it", "is the release ready to cut"):
            rc, out, err = run_door(self.door_path, "--route", ask, cwd=ROOT)
            self.assertEqual(rc, 0, (ask, out, err))
            self.assertIn("(route: %s)" % door.PUBLISH_ROUTE, out, ask)
            self.assertIn(door.CUT_DRIVER, out, ask)

    def test_a_review_worded_release_ask_is_not_routed_to_the_cut(self):
        rc, out, err = run_door(self.door_path, "--route", "check the release", cwd=ROOT)
        self.assertEqual(rc, 2, (out, err))
        self.assertNotIn(door.CUT_DRIVER, out)

    def test_a_publish_word_beside_another_deliver_route_stays_with_that_route(self):
        """"publish the handover" is closer to team-handover, a deliver route of its own, so it never reaches the cut."""
        rc, out, err = run_door(self.door_path, "--route", "publish the handover", cwd=ROOT)
        self.assertNotIn(door.CUT_DRIVER, out + err)
        self.assertNotIn("(route: %s)" % door.PUBLISH_ROUTE, out)

    def test_the_registry_still_carries_the_cut_route_under_the_maintainer_tier(self):
        """The premise of the carve-out: cut-release is a deliver route and its group is filed MAINTAINER."""
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        cap = [c for c in data["capabilities"] if c.get("id") == door.PUBLISH_ROUTE]
        self.assertEqual([c.get("verb") for c in cap], ["deliver"])
        groups = [r["id"] for r in data["inventory"] if r.get("routed") == door.PUBLISH_ROUTE]
        with open(os.path.join(os.path.dirname(door.REACH_REGISTRY), door.ADMISSION_FILE), encoding="utf-8") as fh:
            entries = json.load(fh)["entries"]
        self.assertEqual([entries[g]["tier"] for g in groups], ["MAINTAINER"])


class Detection(unittest.TestCase):
    """maintainer_checkout: every case the detection cannot decide reads as installed."""
    def tree(self, folder="scripts", git=True, driver="file"):
        root = os.path.realpath(tempfile.mkdtemp(prefix="key1d-tree-"))
        self.addCleanup(shutil.rmtree, root, True)
        here = os.path.join(root, folder)
        os.makedirs(here)
        if git:
            with open(os.path.join(root, ".git"), "w") as fh:
                fh.write("gitdir: elsewhere\n")
        path = os.path.join(here, door.CUT_DRIVER)
        if driver == "file":
            with open(path, "w") as fh:
                fh.write("# cut driver\n")
        elif driver == "symlink":
            with open(path + ".real", "w") as fh:
                fh.write("# cut driver\n")
            os.symlink(path + ".real", path)
        elif driver == "dir":
            os.makedirs(path)
        return root, here

    def test_a_checkout_shape_is_a_maintainer_checkout(self):
        self.assertTrue(door.maintainer_checkout(self.tree()[1]))

    def test_no_git_beside_scripts_reads_installed(self):
        self.assertFalse(door.maintainer_checkout(self.tree(git=False)[1]))

    def test_a_runtime_folder_reads_installed_even_with_the_driver_and_git(self):
        self.assertFalse(door.maintainer_checkout(self.tree(folder="runtime")[1]))

    def test_a_missing_driver_reads_installed(self):
        self.assertFalse(door.maintainer_checkout(self.tree(driver="none")[1]))

    def test_a_symlinked_driver_reads_installed(self):
        self.assertFalse(door.maintainer_checkout(self.tree(driver="symlink")[1]))

    def test_a_driver_that_is_a_folder_reads_installed(self):
        self.assertFalse(door.maintainer_checkout(self.tree(driver="dir")[1]))

    def test_a_scripts_folder_reached_through_a_symlink_reads_installed(self):
        root, here = self.tree()
        link = os.path.join(root, "link")
        os.symlink(root, link)
        self.assertFalse(door.maintainer_checkout(os.path.join(link, "scripts")))

    def test_an_unreadable_tree_reads_installed(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root reads everything")
        root, here = self.tree()
        os.chmod(here, 0)
        self.addCleanup(os.chmod, here, 0o755)
        self.assertFalse(door.maintainer_checkout(here))

    def test_a_non_path_reads_installed(self):
        for bad in (None, 7, b"scripts", ""):
            self.assertFalse(door.maintainer_checkout(bad), bad)


def view(routes=(), queue=()):
    return {"candidate": "rev", "host": "host", "capabilities": [], "routes": set(routes), "queue": list(queue)}


INVENTORY = [{"id": "release-cut", "routed": "cut-release", "unit": "KEY1.d", "disposition": "pending"}]


class ThroughAdmission(unittest.TestCase):
    """publish_route delivers only when the maintainer checkout AND OUT1.n's view agree."""
    def test_maintainer_tier_queue_plus_checkout_delivers(self):
        with mock.patch.object(door, "maintainer_checkout", return_value=True):
            got = door.publish_route(view(queue=[{"id": "release-cut", "cause": door.MAINTAINER_TIER_CAUSE + "; x"}]),
                                     INVENTORY)
        self.assertEqual((got["delivered"], got["route"]), (True, door.PUBLISH_ROUTE))

    def test_an_admitted_route_plus_checkout_delivers(self):
        with mock.patch.object(door, "maintainer_checkout", return_value=True):
            got = door.publish_route(view(routes=[door.PUBLISH_ROUTE]), INVENTORY)
        self.assertTrue(got["delivered"])

    def test_a_parked_or_otherwise_hidden_group_never_delivers_even_in_a_checkout(self):
        for cause in ("tier PARK is internal only; x", "no admission record", "", None):
            with mock.patch.object(door, "maintainer_checkout", return_value=True):
                got = door.publish_route(view(queue=[{"id": "release-cut", "cause": cause}]), INVENTORY)
            self.assertEqual((got["delivered"], got["route"], got["action"]), (False, None, door.PUBLISH_LIMIT), cause)

    def test_outside_a_checkout_nothing_delivers(self):
        with mock.patch.object(door, "maintainer_checkout", return_value=False):
            for v in (view(routes=[door.PUBLISH_ROUTE]),
                      view(queue=[{"id": "release-cut", "cause": door.MAINTAINER_TIER_CAUSE}])):
                got = door.publish_route(v, INVENTORY)
                self.assertEqual((got["delivered"], got["route"], got["action"]), (False, None, door.PUBLISH_LIMIT))

    def test_a_queue_row_of_another_group_does_not_count(self):
        with mock.patch.object(door, "maintainer_checkout", return_value=True):
            got = door.publish_route(view(queue=[{"id": "other", "cause": door.MAINTAINER_TIER_CAUSE}]), INVENTORY)
        self.assertFalse(got["delivered"])

    def test_the_library_read_is_unchanged(self):
        """recommend() with no admission view is the checker's library read (OUT1.a): it never enters the publish
        route, so reach_check keeps seeing the registry entry as it is."""
        with mock.patch.object(door, "publish_route", side_effect=AssertionError("entered")):
            got = door.recommend("is the release ready to cut")
        self.assertEqual(got["route"], door.PUBLISH_ROUTE)

    def test_publish_is_a_deliver_cue(self):
        self.assertEqual(door.route("publish it"), "deliver")
        self.assertEqual(door.route("publish the checked result now"), "help")  # "checked" ties the verb
        self.assertTrue(door._publish_ask("deliver", {"publi"}, None))
        self.assertTrue(door._publish_ask("deliver", set(), {"id": door.PUBLISH_ROUTE, "verb": "deliver"}))
        self.assertTrue(door._publish_ask("help", {"publi", "check"}, {"id": "check-a-number", "verb": "review"}))
        self.assertFalse(door._publish_ask("review", {"publi"}, {"id": door.PUBLISH_ROUTE, "verb": "deliver"}))
        self.assertFalse(door._publish_ask("deliver", {"publi", "hando"}, {"id": "team-handover", "verb": "deliver"}))
        self.assertFalse(door._publish_ask("status", {"publi"}, None))


if __name__ == "__main__":
    unittest.main()
