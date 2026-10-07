#!/usr/bin/env python3
"""Unit L1b done check. The subject and the verdict live in scripts/donecheck_units.py; this file is the command the plan names."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donecheck_units
code, line = donecheck_units.run('L1b')
print(line)
sys.exit(code)
