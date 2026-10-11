# Brother

The public home of Brother: one install, one door, two products (BrotherMode
and BrotherSBE) shipping from the same repository and the same release.

- This file is the exported public variant of the private working repository's
  own orientation file; a session working on Brother itself reads a different,
  private PROJECT.md that never leaves that repository. Nothing here should be
  read as internal planning: it is the public repository describing itself.
- Install and the one door: see README.md.
- Each product's own reference documentation lives under its own subtree:
  products/brothermode/docs and products/brothersbe/docs.
- This repository's public pre-merge gate, the one a clone of this tree runs:
  `sh scripts/required_fast.sh`, from the repository root. The `required-fast`
  workflow runs the same script on pull requests to `main`. It drives the
  delivery engine with scripted stand-ins for the model, so it proves the
  mechanism, not a real model's behavior, speed or quality. Exit 0: no check
  failed and every NO-DATA row is allowed by `scripts/gate_obligations.json`.
  Exit 1: a check failed or a NO-DATA row is unexplained. Exit 2: the script
  refused to start. A failed check's full output is saved under `$TMPDIR` (or
  `/tmp`) and its summary line names the file. A few rows read NO-DATA on this
  tree (the plugin runtime tests, which the export ships without, and
  `plugin-manifest` when no Claude Code CLI is found); they are listed and
  never counted as passes. Three results stay separate: this gate, the live
  client and host checks (the next item, and the signed in host proof HP1 in
  `docs/VERSIONING.md`, which reads NO-DATA until it is recorded), and the
  human acceptance trial, which reads NO-DATA until a person runs it.
- The live client check, which needs the `claude` binary on PATH and exits 2
  (BLOCKED, not a pass) without one:
  `sh scripts/bundle-install-smoke.sh` (add `--github` to prove the published
  copy instead of the local clone). It installs the bundle plugin into a
  throwaway configuration directory, confirms every promised entry point
  registers (discovery only, presence asserted, not behaviour), and
  uninstalls clean. Proven in path mode
  against this tree on 2026-09-03 (commit 0ac3688b): `PASSED: one command
  installed the bundle plus brothermode 3.4.5 and brothersbe 3.7.4, uninstall
  clean (discovery only: presence of 2 entries asserted, behaviour not)`,
  exit 0. `--github` mode proves the same claim against the published copy
  instead, and needs the release tag to exist to resolve. The verdict is
  re-run and re-quoted at every release.
- Each product also ships its own deeper test and eval suites (for example
  products/brothermode/tools/test_all.py and
  products/brothersbe/evals/run_evals.py). Those are maintainer regression
  batteries: they expect that product's own full development checkout (its
  complete internal doc and process tree, and real git history), not a
  trimmed install, so they are not the check a public install runs. A
  contributor working on one of the products directly should clone that
  product's own upstream development history rather than run its suite from
  here.
