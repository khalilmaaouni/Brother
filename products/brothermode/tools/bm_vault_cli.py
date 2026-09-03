#!/usr/bin/env python3
"""bm_vault_cli: the vault's one front door.

WHY THIS EXISTS. The vault grew 25+ bm_vault_* tools in tools/, each its own CLI
(bm_vault.py recall, bm_vault_graph.py check, bm_vault_temporal.py check/asof,
bm_vault_lint.py check/fix, bm_vault_curate.py find/list/accept/reject, and more). A
fresh Claude session or a worktree-fenced agent has no discovery path and no single
entry: it learns the estate by refusal (a report-only default it did not expect, a
bake it forgot to run before a vault commit, a git operation a fence hands back).
This module is the smallest honest fix: a thin router over the EXISTING tools (it
never reimplements one), plus a `doctor` verb that answers "where am I, what is here,
is it healthy, what may I not do" in one read-only call.

  recall <query> [--limit N] [...]   -> bm_vault.py recall --query <query> [...]
  check [--vault V] [--paths ...]    -> bm_vault_graph.py check
  measure [--vault V] [--json]       -> bm_vault_graph.py measure
  census [--vault V]                 -> bm_vault_retention.py census
  posture [--vault V]                -> bm_vault_posture.py report
  lint <check|fix> [--vault V] ...   -> bm_vault_lint.py (subcommand is yours to pick)
  contract <check|resolve> [...]     -> bm_vault_contract.py (subcommand is yours to pick)
  curate <find|list|accept|reject>   -> bm_vault_curate.py (subcommand is yours to pick)
  commit --vault V -m MSG [--dry-run] -> bake, gate, and commit the vault, see below
  doctor                             -> read-only report, see below

Every routed verb passes its remaining arguments straight through to the sibling
file, over the SAME python3 interpreter, and exits with the child's exit code:
this file adds discovery, never a second copy of the logic. A child killed by a
signal (subprocess.call returns the negative signal number, e.g. -15 for SIGTERM)
is remapped to the conventional 128 + signal (143), never passed through raw,
since sys.exit() truncates a negative code to a misleading positive byte
(sys.exit(-15) reads as 241, not 143). `recall` is
the one convenience: its first non-flag argument becomes bm_vault.py's --query, so
`bm_vault_cli.py recall "the symptom in words"` works without spelling out the flag;
pass --query yourself and it is left alone.

DEVIATION FROM THE ORIGINAL BRIEF, recorded rather than silently fixed (the estate's
own "a plan is code and its commands can be fiction" lesson): the brief named `census`
as a bm_vault_temporal.py verb and `curate` as a bm_vault_curate.py "scan" verb.
Neither exists (checked with grep over the real argparse subparsers before writing
this file): bm_vault_temporal.py only takes check/asof, and census actually lives on
bm_vault_retention.py; bm_vault_curate.py's real verbs are find/list/accept/reject,
with no "scan". This router points `census` at the file that really has it and leaves
`curate` (and `lint`, which the brief never named a forced subcommand for either) to
pass its subcommand through verbatim, so both verbs work rather than failing exit 2
on every call.

`commit` is the one command for "I edited the vault, land it": in order, (1)
bake catalogs with bm_vault_catalog.py's id-preserving bake, (2) `git add -A`
restricted by the same secret-shaped exclusion pathspecs bm_autosave.py's
snapshot step uses (imported from that file by path, never duplicated), so
this staging step's defense against a credential-shaped file does not depend
on the vault's own .gitignore alone, (3) run tools/bm_vault_tiers.py's
severity-tiered gate (VB10-01) over what is actually staged: broken links,
bad frontmatter, staleness and rot only refuse the commit when the defect
sits on a note NEW in this commit, a defect on a pre-existing note downgrades
to WARN and is queued rather than blocking, and `--quarantine` diverts a
refused new note into 00-Inbox/quarantine/ so the rest of the commit can
land; then the advisory foreign-writer-lock warning
bm_vault_precommit_hook.py itself prints (never blocking, per that file's own
contract), (4) `git commit -m MESSAGE`. The commit MESSAGE is scanned first,
before any of the above runs, with the exact function
scripts/bm_commit_msg_hook.py's own commit-msg hook uses (em dash, en dash, a
`Co-Authored-By:` line): a bad message refuses before a single byte is
baked, staged, or written. A gate refusal at the bm_vault_tiers.py step
unstages what this call staged (`git reset`) and aborts with that gate's own
findings, but never reverts the bake itself: the baked catalog files this
call already wrote stay on disk, since they are derived state and reverting
them here could fight another writer touching the same vault; the refusal
names `git -C VAULT status --short` so the residue is visible rather than
silently implied by "nothing committed". A `git commit` subprocess failure
after the gate passed (case 4) also runs `git reset` (quiet) before
returning, and says so in its own error line, so a failed commit call never
leaves the index half staged either way, matching this paragraph. `--dry-run`
runs no writer at all: it prints bm_vault_catalog.py check's own staleness
report plus `git status --porcelain`'s own line count, and returns without
baking, staging, or committing anything.

`doctor` prints, read-only, never writing a vault byte:
  (a) vault path resolution: BM_VAULT_ROOT / BROTHERMODE_VAULT, the installer-written
      ~/.claude/bm_vault.json, and the resolved value, all read by IMPORTING
      bm_vault.py's own _default_vault()/_config() (never re-derived here, per D01:
      environment first, config file second, no guessed default).
  (b) tool inventory: every bm_vault_*.py in tools/ with its module docstring's
      first line (read via ast, so a broken docstring reports NO-DATA, not a crash).
  (c) gate state: note count and broken-link count from `bm_vault_graph.py measure
      --json` against the resolved vault, run read-only; NO-DATA when no vault
      resolves or the child cannot be read.
  (d) the agent rules: report-only defaults, bake catalogs before any vault commit,
      worktree-fenced agents edit files and hand git operations back.

Exit 0 on a routed success or a clean doctor run, 2 on an unknown verb, otherwise
whatever the routed child exited (128 + signal for a signal-killed child, see
`_run`).

Python 3.9, standard library only, no network.
"""
import argparse
import ast
import glob
import json
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(TOOLS_DIR), "scripts")
SELF = os.path.basename(__file__)

# verb -> (sibling file, forced leading subcommand or None). None means the
# caller's own remaining args carry the subcommand (lint's check/fix, curate's
# find/list/accept/reject): there is no single verb that both names would fit.
VERBS = {
    "recall": ("bm_vault.py", "recall"),
    "check": ("bm_vault_graph.py", "check"),
    "measure": ("bm_vault_graph.py", "measure"),
    "census": ("bm_vault_retention.py", "census"),
    "posture": ("bm_vault_posture.py", "report"),
    "lint": ("bm_vault_lint.py", None),
    "contract": ("bm_vault_contract.py", None),
    "curate": ("bm_vault_curate.py", None),
}

AGENT_RULES = """\
report-only defaults: bm_vault_lint.py fix and bm_vault_curate.py accept/reject
  write only under --apply; everything else here (check, measure, census, a bare
  lint fix or curate find/list) reports and touches no vault byte.
bake catalogs before any vault commit: bm_vault_catalog.py bake regenerates
  10-Projects/<slug>/Catalog.md; a commit that changed vault content without a
  fresh bake ships a stale generated file.
worktree-fenced agents edit files and hand git operations back: a session running
  inside its own isolated worktree can Read/Edit/Write vault or repo files freely,
  but a git command whose target is a different worktree or a runtime-computed
  path is refused by the single-writer fence. Do the file work, then hand the
  add/commit/push back to the orchestrator rather than routing around the refusal.
full agent rules, including the bake-before-commit command, the 601-opener
  corruption check, and the cross-project tool guard: docs/VAULT-AGENT-CONTRACT.md
the trust boundary: policy, audit, and quarantine bind the served path
  (recall and the serve layer) only. Obsidian, a shell, or any process
  running as the user reads vault files directly, bypassing all of it.
  docs/VAULT-TRUST-BOUNDARY.md
the retrieval contract: staged retrieval order, identity trim, staleness
  demotion, restriction withholding, echo exclusion, audit-on-serve, each
  rule pinned to a named existing test. docs/RETRIEVAL-RULES.md\
"""


def _run(script, args):
    """Same interpreter, sibling path, verbatim args, child's own exit code, except
    a signal-killed child (subprocess.call returns the negative signal number) is
    remapped to 128 + signal, since sys.exit() would otherwise truncate a negative
    code into an unrelated positive one (-15 -> 241 instead of the conventional 143)."""
    path = os.path.join(TOOLS_DIR, script)
    rc = subprocess.call([sys.executable, path] + args)
    return 128 + abs(rc) if rc < 0 else rc


def _route(verb, rest):
    script, forced = VERBS[verb]
    if verb == "recall":
        if rest and not rest[0].startswith("-"):
            return _run(script, ["recall", "--query", rest[0]] + rest[1:])
        return _run(script, ["recall"] + rest)
    if forced:
        return _run(script, [forced] + rest)
    return _run(script, rest)


def _load_bm_vault():
    """Import bm_vault.py by path (tools/ is not a package), the same pattern
    bm_vault_curate.py and bm_vault_serve.py already use, so this file reuses
    _default_vault()/_config()/CONFIG_PATH rather than re-deriving them."""
    import importlib.util
    path = os.path.join(TOOLS_DIR, "bm_vault.py")
    spec = importlib.util.spec_from_file_location("bm_vault", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_by_path(name, path):
    """Same by-path import technique bm_vault_catalog.py, bm_vault_curate.py, and
    this file's own _load_bm_vault() already use, so the answer never depends on
    the caller's sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_commit_msg_hook():
    return _load_by_path(
        "bm_commit_msg_hook", os.path.join(SCRIPTS_DIR, "bm_commit_msg_hook.py"))


def _load_bm_autosave():
    """Same by-path import as _load_bm_vault(), so `commit`'s `git add -A`
    reuses bm_autosave.py's own SECRET_EXCLUDE_PATHSPECS / _exclude_pathspecs()
    rather than hand-copying the denylist a second time (the estate's own "the
    scanner must contain what it forbids" lesson applies just as much to a
    second staging step as to a first)."""
    return _load_by_path("bm_autosave", os.path.join(TOOLS_DIR, "bm_autosave.py"))


def _load_precommit_hook():
    return _load_by_path(
        "bm_vault_precommit_hook", os.path.join(SCRIPTS_DIR, "bm_vault_precommit_hook.py"))


def _load_tiers():
    return _load_by_path("bm_vault_tiers", os.path.join(TOOLS_DIR, "bm_vault_tiers.py"))


def _doc_first_line(path):
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        doc = ast.get_docstring(tree)
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return "NO-DATA, could not read module docstring"
    if not doc:
        return "NO-DATA, no module docstring"
    return doc.strip().splitlines()[0]


def _gate_counts(stats):
    """Pull note_count/broken_count out of a measure --json result, tolerant of
    either shape: the VB7-02 envelope ({"counts": {...}, ...}) or an older/
    hand-built flat dict. A dict lacking "counts" is read flat. Missing keys
    come back as the string "NO-DATA" so callers never format a missing value
    with %d (that raises TypeError on a string default)."""
    counts = stats.get("counts")
    if not isinstance(counts, dict):
        counts = stats
    return (counts.get("note_count", "NO-DATA"),
            counts.get("broken_count", "NO-DATA"))


def cmd_doctor(argv):
    bm_vault = _load_bm_vault()

    print("== vault path resolution ==")
    env_val = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    print("BM_VAULT_ROOT / BROTHERMODE_VAULT: %s" % (env_val or "unset"))
    cfg = bm_vault._config()
    cfg_vault = cfg.get("vault")
    cfg_vault = cfg_vault if isinstance(cfg_vault, str) and cfg_vault else None
    print("%s -> vault: %s" % (bm_vault.CONFIG_PATH, cfg_vault or "unset"))
    resolved = bm_vault._default_vault()
    print("resolved: %s" % (resolved or "NO-DATA, nothing configured "
                             "(no env var, no config value)"))

    print()
    print("== tool inventory ==")
    tool_paths = sorted(
        p for p in glob.glob(os.path.join(TOOLS_DIR, "bm_vault_*.py"))
        if os.path.basename(p) != SELF)
    for path in tool_paths:
        print("%s: %s" % (os.path.basename(path), _doc_first_line(path)))
    print("%d tool(s)" % len(tool_paths))

    print()
    print("== gate state ==")
    if not resolved or not os.path.isdir(resolved):
        print("NO-DATA, no readable vault to measure")
    else:
        graph = os.path.join(TOOLS_DIR, "bm_vault_graph.py")
        try:
            proc = subprocess.run(
                [sys.executable, graph, "measure", "--vault", resolved, "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=120)
            stats = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            stats = None
        if stats is None:
            print("NO-DATA, bm_vault_graph.py measure did not return readable JSON")
        else:
            note_count, broken_count = _gate_counts(stats)
            print("notes: %s" % note_count)
            print("broken links: %s" % broken_count)

    print()
    print("== encryption posture ==")
    if not resolved or not os.path.isdir(resolved):
        print("NO-DATA, no readable vault to measure")
    else:
        try:
            posture = _load_by_path(
                "bm_vault_posture", os.path.join(TOOLS_DIR, "bm_vault_posture.py"))
            verdict, detail = posture.storage_state(resolved)
            print("storage: %s (%s)" % (verdict, detail))
        except Exception as e:
            print("NO-DATA, bm_vault_posture.py could not run (%s)" % e)
    print("full storage-encryption state and derived-store census: "
          "bm_vault_cli.py posture report")

    print()
    print("== agent rules ==")
    print(AGENT_RULES)
    return 0


def _build_commit_parser():
    p = argparse.ArgumentParser(prog="bm_vault_cli.py commit",
                                 description="bake, gate, and commit the vault")
    p.add_argument("--vault", default=None)
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quarantine", action="store_true",
                   help="divert a new note refused by the tiered gate into "
                        "00-Inbox/quarantine/ so the rest of the commit lands")
    return p


def _commit_dry_run(vault):
    """Read-only: bm_vault_catalog.py check (never writes) for staleness, plus
    `git status --porcelain` for what `git add -A` would stage. No bake, no add,
    no commit."""
    print("dry run: vault=%s" % vault)
    print()
    print("-- catalog staleness (bm_vault_catalog.py check) --")
    catalog = os.path.join(TOOLS_DIR, "bm_vault_catalog.py")
    subprocess.call([sys.executable, catalog, "check", "--vault", vault])  # sbe: allow-silent read-only preview streams its own diagnostics straight to stdout; dry-run always returns 0 since nothing was written
    print()
    print("-- files `git add -A` would stage --")
    try:
        proc = subprocess.run(["git", "-C", vault, "status", "--porcelain"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
    except OSError as exc:
        print("NO-DATA, could not run git status: %s" % exc)
        return 0
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    for l in lines:
        print(l)
    print("%d file(s) would be staged" % len(lines))
    print()
    print("dry run: nothing baked, staged, or committed")
    return 0


def cmd_commit(argv):
    args = _build_commit_parser().parse_args(argv)
    bm_vault = _load_bm_vault()
    vault = args.vault or bm_vault._default_vault()
    if not vault:
        sys.stderr.write(
            "bm_vault_cli commit: NO-DATA, no vault resolved (set BM_VAULT_ROOT / "
            "BROTHERMODE_VAULT, or pass --vault)\n")
        return 2
    if not os.path.isdir(vault):
        sys.stderr.write("bm_vault_cli commit: NO-DATA, %r is not a directory\n" % vault)
        return 2

    # Message gate first, before a single byte is baked, staged, or written: pure,
    # no I/O, same check scripts/bm_commit_msg_hook.py's own commit-msg hook runs.
    msg_hook = _load_commit_msg_hook()
    reason = msg_hook.check_message(args.message)
    if reason is not None:
        sys.stderr.write("bm_vault_cli commit: REFUSING this commit message, %s\n" % reason)
        return 1

    if args.dry_run:
        return _commit_dry_run(vault)

    catalog = os.path.join(TOOLS_DIR, "bm_vault_catalog.py")

    bake_rc = subprocess.call([sys.executable, catalog, "bake", "--vault", vault])
    if bake_rc != 0:
        sys.stderr.write(
            "bm_vault_cli commit: bake refused (bm_vault_catalog.py bake exit %d, "
            "see above). Nothing staged, nothing committed.\n" % bake_rc)
        return 1

    # Same secret-shaped exclusion pathspecs bm_autosave.py's own snapshot
    # staging step uses, imported by path rather than duplicated: defense
    # against a credential-shaped file in the vault must not depend on the
    # vault's own .gitignore alone.
    autosave = _load_bm_autosave()
    add_rc = subprocess.call(
        ["git", "-C", vault, "add", "-A", "--", "."] + list(autosave._exclude_pathspecs()))
    if add_rc != 0:
        sys.stderr.write("bm_vault_cli commit: git add -A failed (exit %d).\n" % add_rc)
        return 1

    # VB10-01: the severity-tiered write gate replaces a bare bm_vault_graph.py
    # check here. That call was UNSCOPED (the whole vault, every time), so a
    # broken link on a note nobody touched this commit could refuse a commit
    # that never went near it. tools/bm_vault_tiers.py runs the same checks
    # restricted to what is actually staged, and downgrades a defect already
    # present on a pre-existing note to WARN (queued, never blocking); only a
    # defect on a note NEW in this commit still refuses.
    tiers = _load_tiers()
    gate_rc, gate_text = tiers.run_gate(vault, quarantine=args.quarantine)
    if gate_text:
        sys.stderr.write(gate_text + "\n")
    if gate_rc != 0:
        reset_rc = subprocess.call(["git", "-C", vault, "reset"])
        if reset_rc != 0:
            sys.stderr.write(
                "bm_vault_cli commit: WARNING, git reset failed (exit %d) after "
                "the gate refused; the index may still be staged -- check with "
                "git -C %s status --short\n" % (reset_rc, vault))
        sys.stderr.write(
            "bm_vault_cli commit: gate refused (bm_vault_tiers exit %d, findings "
            "named above). Fix the note(s) named above, or re-run with "
            "--quarantine to divert a new offending note into "
            "00-Inbox/quarantine/ and land the rest of the commit. Nothing "
            "committed. The bake that ran before this gate already wrote baked "
            "catalog files into the working tree and this refusal does NOT "
            "revert them (they are derived state; reverting could fight "
            "another writer touching the same vault) -- see what changed "
            "with: git -C %s status --short\n" % (gate_rc, vault))
        return 1

    # Advisory only, per bm_vault_precommit_hook.py's own contract: it never
    # blocks, so its text (if any) is printed and the commit proceeds regardless.
    precommit = _load_precommit_hook()
    warning = precommit.warning_for(vault)
    if warning:
        sys.stderr.write(warning + "\n")

    commit_rc = subprocess.call(["git", "-C", vault, "commit", "-m", args.message])
    if commit_rc != 0:
        reset_rc = subprocess.call(["git", "-C", vault, "reset", "-q"])
        if reset_rc != 0:
            sys.stderr.write(
                "bm_vault_cli commit: WARNING, git reset -q failed (exit %d) after "
                "the commit failed; the index may still be staged -- check with "
                "git -C %s status --short\n" % (reset_rc, vault))
        sys.stderr.write(
            "bm_vault_cli commit: git commit failed (exit %d); unstaged the index "
            "(git reset) so this call never leaves it half staged. Nothing "
            "committed.\n" % commit_rc)
        return 1
    return 0


def _usage():
    return ("usage: bm_vault_cli.py <verb> [args...]\n"
            "verbs: %s, commit, doctor\n" % ", ".join(sorted(VERBS)))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_usage())
        return 0
    verb, rest = argv[0], argv[1:]
    if verb == "doctor":
        return cmd_doctor(rest)
    if verb == "commit":
        return cmd_commit(rest)
    if verb not in VERBS:
        sys.stderr.write("bm_vault_cli: unknown verb %r\n%s" % (verb, _usage()))
        return 2
    return _route(verb, rest)


if __name__ == "__main__":
    sys.exit(main())
