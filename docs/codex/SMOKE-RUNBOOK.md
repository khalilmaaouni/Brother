# Codex smoke runbook

This proves the installed path is usable; it does not prove every Brother capability.

1. Confirm the Brother plugin is present from the intended marketplace/ref.
2. From a Brother checkout, use `scripts/codex_hooks_install.py --trust` and accept hook wiring only when the installer says Codex sees the expected Brother hooks trusted/enabled.
3. Use a clean throwaway target repository. Keep plan/contract scratch and run roots outside it when required.
4. Run a discriminating outcome. At repository-engine level:

```bash
python3 scripts/brother_run.py "<outcome>" --cwd <repo>
```

If the current turn cannot spawn nested model workers, follow the installed `using-brother` in-session plan/worktree handback route rather than repeatedly launching nested clients.

5. Read the emitted receipt back. Process exit alone is not the smoke proof.
6. If removing the smoke wiring, use `scripts/codex_hooks_install.py --uninstall` so only Brother-owned hook entries are removed.

## The signed-in smoke, exact commands

In a throwaway directory, create the toy.

```bash
mkdir toy && cd toy && git init -q .
printf 'def add(a, b):\n    return a + b\n' > mathlib.py
printf 'import unittest\n\nfrom mathlib import add\n\n\nclass AddTest(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n\nif __name__ == "__main__":\n    unittest.main()\n' > test_mathlib.py
git add -A && git commit -q -m toy
```

The test uses `unittest`, so the toy needs nothing installed. NOTHING ELSE IS WRITTEN INTO THE TOY: no STATE.md, no `.sbe/`, no fence file. Such files dirty the tree before the first unit and integration refuses every unit.

```bash
codex exec -s workspace-write -c "sandbox_workspace_write.writable_roots=[\"$PWD/.git\"]" -C "$PWD" "use the Brother plugin to make add() refuse non-numeric input and cover it with a test, tests run with python3 -m unittest"
```

Codex defaults to read-only, so `-s workspace-write` is required. The `.git` root allows Brother's `git worktree add`; without it every unit is refused. `sandbox_workspace_write.network_access=true` is deliberately not included: it opens sockets, but nested `codex exec` cannot start inside a Codex turn ("failed to initialize in-process app-server client").

```bash
MODEL_WORKER_CMD="python3 write_the_change.py" \
    python3 "$BROTHER_PLUGIN_ROOT/runtime/brother_run.py" "<outcome>" \
    --cwd "$PWD" --plan plan.json \
    --runs-root "${CODEX_HOME:-$HOME/.codex}/brother/runs"
```

plan.json is a JSON list of units with `id`, `objective`, `done_check`, `writes`, and `deps`. `DOOR_MODEL_CMD="cat plan.json"` is the same hand-over in environment form, for the decomposer seam. The runs root stays outside the toy, because records inside it dirty the tree and a read-only plugin install cannot write its own tree.

- Each `done_check` must fail BEFORE any work happens. Never write a bare-path check: it is judged on its RESULT, never on a missing file.
- `writes` must name EVERY file changed or created, including mathlib.py and test_mathlib.py. Out-of-scope changes read QUARANTINE, never integrated.
- The script in MODEL_WORKER_CMD edits only those files and must exit 0. A NO-DATA receipt is not a forcing condition: fix the check or script and rerun without asking.

PASS means no 401, a `brother_run: receipt: <path>` that exists, and a receipt naming mathlib.py, the deciding check command, and an exit code. Exit 0 alone proves nothing, because writes outside granted roots are silently dropped.
