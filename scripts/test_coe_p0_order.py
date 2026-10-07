"""Tests for scripts/coe_p0_order.py, sub unit P0.2.

The module under test is loaded from its own file beside this suite, so this
suite needs no repository document, no live board, no HOME and no
environment variable. Every fixture is built in a temp folder.
"""

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_FILE = os.path.join(HERE, "coe_p0_order.py")
MODULE_NAME = "coe_p0_order_under_test"

COUNCIL_ORDER = ["D3", "D10", "D4", "D11", "D12", "D13", "C0"]
OFF_CHAIN_HEADER = "## Off chain and absent from the finish order"


def _load_module():
    if not os.path.isfile(MODULE_FILE):
        return None
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_FILE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit(unit_id, state):
    return {"id": unit_id, "state": state}


def board_units(states=None):
    given = dict(states or {})
    units = []
    for unit_id in COUNCIL_ORDER:
        units.append(unit(unit_id, given.get(unit_id, "SPECIFIED")))
    units.append(unit("D1", given.get("D1", "DONE")))
    units.append(unit("D6", given.get("D6", "SPECIFIED")))
    return units


def write_board(folder, units, name="WBS.json"):
    payload = {"schema": "brother-orch-wbs-v1", "units": units}
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(json.dumps(payload).encode("utf-8"))
    return path


def finish_section(text):
    return text.split(OFF_CHAIN_HEADER)[0]


def off_chain_section(text):
    return text.split(OFF_CHAIN_HEADER)[1]


def order_ids(text):
    ids = []
    for line in finish_section(text).splitlines():
        if ", state" not in line:
            continue
        head = line.split(", state", 1)[0]
        ids.append(head.split(". ", 1)[1])
    return ids


class TempFolderCase(unittest.TestCase):
    def setUp(self):
        self._order_module = None

    def order(self):
        if self._order_module is None:
            module = _load_module()
            if module is None:
                self.fail("module under test is missing or not loadable: %s" % MODULE_FILE)
            self._order_module = module
        return self._order_module

    def make_folder(self, prefix):
        holder = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(holder.cleanup)
        return holder.name


class TestRenderStandingOrder(TempFolderCase):
    def setUp(self):
        TempFolderCase.setUp(self)
        self.folder = self.make_folder("p02-render-")
        self.path = write_board(self.folder, board_units())

    def test_module_order_constant_is_the_council_order(self):
        order = self.order()
        self.assertEqual(list(order.ORDER_UNITS), COUNCIL_ORDER)

    def test_order_is_the_council_order(self):
        order = self.order()
        text = order.render_standing_order(self.path)
        self.assertEqual(order_ids(text), COUNCIL_ORDER)

    def test_c0_waits_on_l5d_noted(self):
        order = self.order()
        text = order.render_standing_order(self.path)
        self.assertIn("7. C0, state SPECIFIED, waits on L5d", text)

    def test_done_unit_not_in_finish_order(self):
        order = self.order()
        path = write_board(self.folder, board_units({"D3": "DONE"}), name="done.json")
        finish = finish_section(order.render_standing_order(path))
        self.assertNotIn("D3, state", finish)
        self.assertIn("1. D10, state SPECIFIED", finish)

    def test_off_chain_units_named(self):
        order = self.order()
        text = order.render_standing_order(self.path)
        section = off_chain_section(text)
        self.assertIn("- D6, state SPECIFIED", section)
        self.assertNotIn("- D1, state", section)

    def test_off_chain_excludes_finish_order_units(self):
        order = self.order()
        section = off_chain_section(order.render_standing_order(self.path))
        for unit_id in COUNCIL_ORDER:
            self.assertNotIn("- %s, state" % unit_id, section)

    def test_digest_present(self):
        order = self.order()
        text = order.render_standing_order(self.path)
        with open(self.path, "rb") as handle:
            raw = handle.read()
        self.assertIn("source digest: sha256:%s" % hashlib.sha256(raw).hexdigest(), text)
        self.assertNotIn("source digest: missing", text)


class TestRenderRefusals(TempFolderCase):
    def setUp(self):
        TempFolderCase.setUp(self)
        self.folder = self.make_folder("p02-refusal-")

    def test_empty_unit_list_refused(self):
        order = self.order()
        path = write_board(self.folder, [])
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_corrupt_json_refused(self):
        order = self.order()
        path = os.path.join(self.folder, "corrupt.json")
        with open(path, "wb") as handle:
            handle.write(b"{ this is not json")
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_non_utf8_refused(self):
        order = self.order()
        path = os.path.join(self.folder, "binary.json")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00{\x01")
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_missing_file_refused(self):
        order = self.order()
        with self.assertRaises(ValueError):
            order.render_standing_order(os.path.join(self.folder, "absent.json"))

    def test_directory_refused(self):
        order = self.order()
        with self.assertRaises(ValueError):
            order.render_standing_order(self.folder)

    def test_units_not_a_list_refused(self):
        order = self.order()
        path = os.path.join(self.folder, "units.json")
        with open(path, "wb") as handle:
            handle.write(b'{"units": {"id": "D3"}}')
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_unit_missing_id_refused(self):
        order = self.order()
        path = write_board(self.folder, [{"state": "SPECIFIED"}])
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_unit_id_wrong_type_refused(self):
        order = self.order()
        for bad_id in [7, None, "", ["D3"], True]:
            path = write_board(self.folder, [{"id": bad_id, "state": "SPECIFIED"}], name="badid.json")
            with self.assertRaises(ValueError):
                order.render_standing_order(path)

    def test_unit_not_an_object_refused(self):
        order = self.order()
        path = write_board(self.folder, ["D3"])
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_duplicate_unit_id_refused(self):
        order = self.order()
        units = board_units()
        units.append(unit("D3", "SPECIFIED"))
        path = write_board(self.folder, units, name="dupe.json")
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_missing_finish_order_unit_refused(self):
        order = self.order()
        path = write_board(self.folder, [unit("D3", "SPECIFIED")], name="short.json")
        with self.assertRaises(ValueError):
            order.render_standing_order(path)

    def test_bad_state_refused(self):
        order = self.order()
        for bad_state in [7, None, "", True, ["DONE"]]:
            path = write_board(self.folder, board_units({"D3": bad_state}), name="badstate.json")
            with self.assertRaises(ValueError):
                order.render_standing_order(path)

    def test_board_root_not_object_refused(self):
        order = self.order()
        for payload in [b"[]", b'"a string"', b"7", b"null", b"true"]:
            path = os.path.join(self.folder, "root.json")
            with open(path, "wb") as handle:
                handle.write(payload)
            with self.assertRaises(ValueError):
                order.render_standing_order(path)

    def test_hostile_paths_refused(self):
        order = self.order()
        for value in [None, 7, 3.5, True, False, [], {}, b"WBS.json", float("nan")]:
            with self.assertRaises(ValueError):
                order.render_standing_order(value)
            with self.assertRaises(ValueError):
                order.publish_standing_order(value)

    def test_empty_path_refused(self):
        order = self.order()
        with self.assertRaises(ValueError):
            order.render_standing_order("")

    def test_null_byte_path_refused(self):
        order = self.order()
        with self.assertRaises(ValueError):
            order.render_standing_order(os.path.join(self.folder, "a\x00b.json"))


class TestPublishStandingOrder(TempFolderCase):
    def setUp(self):
        TempFolderCase.setUp(self)
        self.folder = self.make_folder("p02-publish-")
        self.path = write_board(self.folder, board_units())

    def target(self):
        order = self.order()
        return os.path.join(self.folder, order.ORDER_DIR_NAME, order.ORDER_FILE_NAME)

    def test_publish_writes_order_beside_the_board(self):
        order = self.order()
        result = order.publish_standing_order(self.path)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 0)
        self.assertTrue(os.path.isfile(self.target()))

    def test_publish_second_time_reports_done(self):
        order = self.order()
        self.assertEqual(order.publish_standing_order(self.path), 0)
        with open(self.target(), "rb") as handle:
            first = handle.read()
        self.assertEqual(order.publish_standing_order(self.path), 2)
        with open(self.target(), "rb") as handle:
            self.assertEqual(handle.read(), first)

    def test_stale_order_regenerated(self):
        order = self.order()
        order.publish_standing_order(self.path)
        with open(self.target(), "wb") as handle:
            handle.write(b"stale text from an older board")
        self.assertEqual(order.publish_standing_order(self.path), 0)
        with open(self.target(), "rb") as handle:
            landed = handle.read()
        self.assertNotEqual(landed, b"stale text from an older board")
        self.assertIn(b"source digest: sha256:", landed)

    def test_published_content_is_the_rendered_text(self):
        order = self.order()
        order.publish_standing_order(self.path)
        expected = order.render_standing_order(self.path).encode("utf-8")
        with open(self.target(), "rb") as handle:
            self.assertEqual(handle.read(), expected)

    def test_done_units_filtered_out_of_the_published_finish_order(self):
        order = self.order()
        path = write_board(self.folder, board_units({"D3": "DONE", "D10": "DONE"}), name="filtered.json")
        order.publish_standing_order(path)
        with open(self.target(), "rb") as handle:
            finish = finish_section(handle.read().decode("utf-8"))
        self.assertNotIn("D3, state", finish)
        self.assertNotIn("D10, state", finish)
        self.assertIn("1. D4, state SPECIFIED", finish)

    def test_last_writer_wins(self):
        order = self.order()
        order.publish_standing_order(self.path)
        with open(self.target(), "rb") as handle:
            first = handle.read()
        write_board(self.folder, board_units({"D3": "DONE"}))
        self.assertEqual(order.publish_standing_order(self.path), 0)
        with open(self.target(), "rb") as handle:
            second = handle.read()
        self.assertNotEqual(first, second)
        self.assertIn("1. D10, state SPECIFIED", second.decode("utf-8"))

    def test_publish_corrupt_board_refused(self):
        order = self.order()
        path = os.path.join(self.folder, "corrupt.json")
        with open(path, "wb") as handle:
            handle.write(b"{ broken")
        with self.assertRaises(ValueError):
            order.publish_standing_order(path)
        self.assertFalse(os.path.exists(self.target()))


if __name__ == "__main__":
    unittest.main()
