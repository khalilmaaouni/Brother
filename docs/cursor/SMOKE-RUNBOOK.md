# Cursor smoke runbook

This proves the installed path is usable; it does not prove every Brother capability.

## Install

From the Brother checkout root:

```bash
python3 scripts/cursor_plugin_install.py
python3 scripts/cursor_plugin_install.py validate
python3 scripts/test_cursor_plugin.py
```

The installer copies the bundle to `~/.cursor/plugins/local/brother`.
Run `Developer: Reload Window` in Cursor. The smoke script supplies its
own temporary plugin directory through `--plugin-dir` to `cursor-agent`.

The current source includes WBS-70 U1/U2's vendor adapter, U3's umbrella
checkout discovery, U4's real mailbox skill aliases, U5's three native
personas, U6's optional MCP configuration/server pair, and U7's reserved
agent events. Persona limits are prompt-level; the reserved events add no
gates. Package checks do not prove live hook enforcement. U8 is the current
documentation pass; U9, final regeneration, has not started.

## The toy

The smoke script creates this toy itself. To inspect it manually, use a
throwaway directory:

```bash
mkdir toy && cd toy && git init -q .
printf 'def add(a, b):\n    return a + b\n' > mathlib.py
printf 'import unittest\n\nfrom mathlib import add\n\n\nclass AddTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n\nif __name__ == "__main__":\n    unittest.main()\n' > test_mathlib.py
git add -A && git commit -q -m toy
```

The test uses `unittest`, so the toy needs nothing installed.

## The signed-in smoke, exact command

Run from the Brother checkout root, not from the manual toy directory.

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

The founder confirmed the install/adapter works on his real machine:
"Cursor is fine mark is as tested", in the
[2026-09-13 decision record](../decisions/cursor-live-canary-2026-09-13.json).
That is the evidence for marking the adapter tested. It supplies no
HOOKS-FIRE, HOOKS-ROOT, EDIT, or RECEIPT output from this script. The
signed-in smoke test has not been run this session, so those automated
results are NO-DATA for this session.

A measured deny is not part of this smoke, and the verbal confirmation
does not supply one. Measured denial remains NO-DATA on this evidence.

<!-- doc-assurance: allow-missing mathlib.py (created in the temporary toy repository by the commands above) -->
