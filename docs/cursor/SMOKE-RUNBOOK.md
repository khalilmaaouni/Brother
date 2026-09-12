# Cursor smoke runbook

This proves the installed path is usable; it does not prove every Brother capability.

## Install

Copy the bundle to `~/.cursor/plugins/local/brother`, then run `Developer: Reload Window` in Cursor. For headless runs, pass `--plugin-dir <path>` to `cursor-agent`.

## The toy

In a throwaway directory, create the toy.

```bash
mkdir toy && cd toy && git init -q .
printf 'def add(a, b):\n    return a + b\n' > mathlib.py
printf 'import unittest\n\nfrom mathlib import add\n\n\nclass AddTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n\nif __name__ == "__main__":\n    unittest.main()\n' > test_mathlib.py
git add -A && git commit -q -m toy
```

The test uses `unittest`, so the toy needs nothing installed.

## The signed-in smoke, exact command

```bash
python3 scripts/cursor_smoke.py --signed-in
```

If `cursor-agent status` says `Not logged in`, the script prints NO-DATA and exits 2. That is not a pass.

## What PASS means

The default run (`python3 scripts/cursor_smoke.py`, no login) passes when a throwaway home reads `Not logged in`, a print turn stops at the authentication boundary, and the founder's `~/.cursor` witness hash is unchanged.

The signed-in run passes when all three verdicts pass and the witness is unchanged:

- HOOKS-FIRE: a canary hook with an absolute path wrote its marker, so Cursor ran the plugin's hooks.
- HOOKS-ROOT: a canary hook written as `${PLUGIN_ROOT}/canary_root.py` wrote its marker, so Cursor expanded `${PLUGIN_ROOT}`. Every shipped Brother hook depends on this.
- EDIT: `mathlib.py` changed and `python3 -m unittest` exits 0 in the toy.

RECEIPT is reported separately: a `brother_run` receipt path appeared in the output and the file exists, or NO-DATA.

A measured deny is not part of this smoke. Until one is, fence enforcement under Cursor stays ADVISORY.

<!-- doc-assurance: allow-missing mathlib.py (created in the temporary toy repository by the commands above) -->
