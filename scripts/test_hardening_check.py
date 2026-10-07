#!/usr/bin/env python3
"""Every declared section must supply and execute its own check."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

class HardeningCheck(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).with_name("hardening_check.py")
        self.assertTrue(path.is_file(), "hardening runner implementation required")
        spec=importlib.util.spec_from_file_location("hardening_check",path)
        self.h=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.h)

    def spec(self, a="python3 -B scripts/test_a.py", b="python3 -B scripts/test_b.py"):
        return "## H3.a First\nDone check:\n\n```text\n%s\n```\n## H3.b Second\nDone check:\n\n```text\n%s\n```\n" % (a,b)

    def test_runs_every_section_even_after_failure(self):
        calls=[]
        def run(argv, **kwargs):
            calls.append(argv)
            return type("Result",(),{"returncode":7 if len(calls)==1 else 0})()
        with mock.patch.object(self.h.subprocess,"run",side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.h.check("H3", self.spec()),1)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][0],sys.executable)

    def test_all_green_passes(self):
        with mock.patch.object(self.h.subprocess,"run",return_value=type("Result",(),{"returncode":0})()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.h.check("H3",self.spec()),0)

    def test_missing_malformed_duplicate_and_unknown_fail(self):
        for spec, unit in ((self.spec()+"## H3.c Missing\n", "H3"), (self.spec(b=""), "H3"), (self.spec(),"H30"), (self.spec()+self.spec(),"H3"), (self.spec(b="python3 scripts/test_b.py; echo pass"),"H3")):
            with self.subTest(spec=spec), mock.patch.object(self.h.subprocess,"run",return_value=type("Result",(),{"returncode":0})()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.h.check(unit,spec),1)

    def test_unlaunchable_check_fails(self):
        with mock.patch.object(self.h.subprocess,"run",side_effect=OSError("fixture failure")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.h.check("H3",self.spec()),1)

if __name__ == "__main__":
    unittest.main()
