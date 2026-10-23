import json
import os
import tempfile
import unittest

import release_body


HOSTILE_VALUES = [
    None,
    0,
    True,
    -1,
    float("nan"),
    "",
    "x",
    b"x",
    [],
    ["x"],
    {},
    {"a": 1},
    (),
    {1, 2},
    object(),
]


class TestI26A(unittest.TestCase):
    def _call(self, fn, *args):
        try:
            fn(*args)
        except (ValueError, LookupError, SystemExit):
            return
        except Exception as exc:
            self.fail("{} crashed with {}: {}".format(fn.__name__, type(exc).__name__, exc))

    def _write_fixtures(self, directory):
        wbs_path = os.path.join(directory, "wbs.json")
        hosts_path = os.path.join(directory, "hosts.txt")
        wbs = {"units": [{"state": "done"}, {"state": "done"}, {"state": "open"}]}
        with open(wbs_path, "w", encoding="utf-8") as handle:
            json.dump(wbs, handle)
        hosts_text = "| host | state |\n| --- | --- |\n| alpha | up |\n| beta | NO-DATA |\n"
        with open(hosts_path, "w", encoding="utf-8") as handle:
            handle.write(hosts_text)
        return wbs_path, hosts_path, wbs, hosts_text

    def test_every_count_in_the_body_equals_the_count_computed_from_the_files(self):
        with tempfile.TemporaryDirectory() as directory:
            wbs_path, hosts_path, wbs, hosts_text = self._write_fixtures(directory)
            prose = "Release body\n{{WBS_TABLE}}\n{{HOSTS_TABLE}}\n"
            body = release_body.compose_release_body(prose, wbs_path, hosts_path)
            release_body.validate_release_body(body, wbs_path, hosts_path)
            wbs_counts = release_body.count_wbs_states(wbs)
            hosts = release_body.read_hosts(hosts_path)
            hosts_counts = release_body.count_host_states(hosts)
            expected_wbs = release_body.render_wbs_table(wbs)
            expected_hosts = release_body.render_hosts_table(hosts)
            self.assertIn(expected_wbs, body)
            self.assertIn(expected_hosts, body)
            self.assertEqual(wbs_counts, {"done": 2, "open": 1})
            self.assertEqual(hosts_counts, {"up": 1, "NO-DATA": 1})

    def test_generated_tables_refuse_a_figure_with_no_source(self):
        with tempfile.TemporaryDirectory() as directory:
            wbs_path, hosts_path, wbs, hosts_text = self._write_fixtures(directory)
            prose = "Release body\n{{WBS_TABLE}}\n{{HOSTS_TABLE}}\n"
            body = release_body.compose_release_body(prose, wbs_path, hosts_path)
            with self.assertRaisesRegex(ValueError, "figure outside"):
                release_body.validate_release_body(body + "\n7\n", wbs_path, hosts_path)
            with self.assertRaisesRegex(ValueError, "table row with no source"):
                release_body.validate_release_body(body + "| extra | state |\n", wbs_path, hosts_path)
            with self.assertRaisesRegex(ValueError, "must not contain a figure"):
                release_body.compose_release_body("Release 2\n{{WBS_TABLE}}\n{{HOSTS_TABLE}}\n", wbs_path, hosts_path)
            with self.assertRaisesRegex(ValueError, "must contain"):
                release_body.compose_release_body("no placeholders", wbs_path, hosts_path)

    def test_hostile_inputs_are_refused_not_crashed(self):
        for value in HOSTILE_VALUES:
            self._call(release_body.read_wbs, value)
            self._call(release_body.read_hosts, value)
            self._call(release_body.count_wbs_states, value)
            self._call(release_body.count_host_states, value)
            self._call(release_body.render_wbs_table, value)
            self._call(release_body.render_hosts_table, value)
            self._call(release_body.compose_release_body, value, value, value)
            self._call(release_body.validate_release_body, value, value, value)


if __name__ == "__main__":
    unittest.main()
