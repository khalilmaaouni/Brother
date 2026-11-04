#!/usr/bin/env python3
"""KEY1.f: every shipped vault default goes through the one resolver.

bm_vault.resolve_vault_root is the one answer to "which vault?" (D01): an
explicit --vault, then BROTHERMODE_VAULT_ROOT, its aliases, then the installer
config, and NO guessed folder. Measured at 0e6b8e87d, dozens of shipped lines
defaulted to one person's own vault folder under ~/Documents instead, so a
stranger's run read, or wrote, a folder they never chose, and the author's
machine layout shipped inside the product. Every such default now calls
pattern_note.require_vault_root (or pattern_note.vault_root where a missing
vault is a reported gap, not a stop), which asks the resolver.

WHAT THIS PROVES, AND HOW.
- The shape scan finds a hardcoded default by its SHAPE, never by the folder's
  name, so this file carries no private term: a home relative literal that
  names a folder directly under Documents whose name says vault, written as a
  literal ("~/Documents/<x> Vault"), a join (HOME, "Documents", "<x> Vault"), a
  path division, or a concatenation. Docstrings are prose, not defaults, and
  are not read. An unparseable shipped file is reported, never skipped.
- The entry point check runs every tool's own CLI in a scratch HOME with every
  vault setting removed: each one exits 2 with a NO-DATA line naming the
  setting, and none of them names a folder of its own.
- A configured vault is used (the canonical name, an alias, the config file),
  two names on two folders FAIL, and a line break inside a setting cannot
  forge a second report line.
- The two pattern_note.py copies (scripts/ and products/brothermode/tools/,
  plus their bundle mirrors) are byte identical, so the one helper cannot
  drift between the trees.

WHAT THIS DOES NOT COVER, named: shell scripts and non Python files (they are
outside the unit's owns), a vault default under a folder other than Documents
(the product's own ~/BrotherModeVault run state, a different concept), and two
maintainer scripts whose CLI cannot run without side effects
(capability_probe.py probes live tools first; cutover_pack.py builds a pack):
both are held by the shape scan only.

Python 3.9, standard library only. No em or en dashes anywhere in this file.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BM_TOOLS = os.path.join(ROOT, "products", "brothermode", "tools")
SBE_TOOLS = os.path.join(ROOT, "products", "brothersbe", "tools")

#: the trees a user's install or a maintainer's checkout runs from
SHIPPED_DIRS = ("products/brothermode/tools", "products/brothermode/scripts",
                "products/brothersbe/tools", "products/brothersbe/scripts",
                "scripts", "bundle")
SKIP_DIRS = {"fixtures", "__pycache__", ".git", "node_modules"}
VAULT_WORD = re.compile(r"vault", re.I)
LITERAL = re.compile(r"~/Documents/[^/\"']*vault", re.I)
#: every name the resolver, or the config it reads, answers to
SETTINGS = ("BROTHERMODE_VAULT_ROOT", "BM_VAULT_ROOT", "BROTHERMODE_VAULT",
            "BROTHERSBE_VAULT", "BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
COPIES = ("scripts/pattern_note.py", "products/brothermode/tools/pattern_note.py",
          "bundle/runtime/pattern_note.py",
          "bundle/runtime/hooks/brothermode/tools/pattern_note.py")


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _tail(node):
    """The last string segment of a path expression: a constant, or the right
    end of a / chain (Path.home() / "Documents")."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _tail(node.right)
    return _str(node)


def _head(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _head(node.left)
    return _str(node)


def _under_documents(parent, child):
    return (parent is not None and child is not None
            and parent.rstrip("/").split("/")[-1] == "Documents"
            and VAULT_WORD.search(child) is not None)


def documents_vault_sites(text, filename="<text>"):
    """Sorted line numbers where `text` defaults to a vault folder directly
    under the home Documents folder. Raises SyntaxError for unparseable text."""
    tree = ast.parse(text, filename=filename)
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and _str(n.value) is not None}
    hits = set()
    for node in ast.walk(tree):
        s = _str(node)
        if s is not None and id(node) not in prose and LITERAL.search(s):
            hits.add(node.lineno)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "join":
            args = node.args
            if any(_under_documents(_str(a), _str(b)) for a, b in zip(args, args[1:])):
                hits.add(node.lineno)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div) and _under_documents(_tail(node.left), _head(node.right)):
                hits.add(node.lineno)
            if isinstance(node.op, ast.Add):
                a, b = _str(node.left), _str(node.right)
                if a is not None and b is not None and LITERAL.search(a + b):
                    hits.add(node.lineno)
    return sorted(hits)


def shipped_files(root=ROOT):
    for rel in SHIPPED_DIRS:
        top = os.path.join(root, rel)
        for dp, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                if f.endswith(".py") and not f.startswith("test_"):
                    yield os.path.join(dp, f)


def scan(root=ROOT):
    """{relative path: [line, ...] or "unparseable: why"} over every shipped
    file. A file that cannot be read or parsed is reported, never clean."""
    out = {}
    for path in shipped_files(root):
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as fh:
                lines = documents_vault_sites(fh.read(), path)
        except (OSError, SyntaxError, ValueError) as exc:
            out[rel] = "unparseable: %s" % type(exc).__name__
            continue
        if lines:
            out[rel] = lines
    return out


def scrubbed_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if k not in SETTINGS}
    env["HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def run(argv, env, cwd=ROOT):
    proc = subprocess.run([sys.executable] + argv, cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


T = "products/brothermode/tools/"
#: every shipped CLI whose default was routed, run the way a user runs it
ENTRY = [
    [T + "bm_vault_catalog.py", "check"],
    [T + "bm_vault_contract.py", "check", "--all"],
    [T + "bm_vault_graph.py", "check"],
    [T + "bm_vault_lint.py", "check"],
    [T + "bm_vault_lock.py", "check"],
    [T + "bm_vault_promote.py", "check"],
    [T + "bm_vault_curate.py", "find"],
    [T + "bm_vault_distill.py", "scan-duplicates"],
    [T + "bm_vault_attributes.py", "codes", "check", "--all"],
    [T + "bm_vault_attribute_provenance.py", "get", "--note", "n", "--attribute", "a"],
    [T + "bm_vault_compose.py", "split", "--note", "n", "--heading", "h", "--today", "2026-01-01"],
    [T + "bm_profile.py", "read", "--project", "p"],
    [T + "find_out.py", "a problem"],
    ["products/brothersbe/tools/bm_profile_reader.py", "read", "--project", "p"],
    ["scripts/find_out.py", "a problem"],
    ["scripts/graph_value_experiment.py"],
    ["scripts/vault_anchor_backfill.py"],
    ["scripts/wisdom_capture.py", "--created", "2026-01-01", "--dry-run", "--lessons", "{home}/lessons.json"],
]


class OneResolver(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="key1f-home-")
        # a folder shaped like a hardcoded default, so a tool that still
        # guessed one would find something to read in the scratch home
        os.makedirs(os.path.join(self.home, "Documents", "Sample Vault", "40-Failures"))
        # wisdom_capture reads its lessons before it asks for the vault; the test owns that input, so the
        # answer never depends on a docs/ file only a checkout has (the hermetic export ships none).
        with open(os.path.join(self.home, "lessons.json"), "w") as fh:
            json.dump({"lessons": [{"name": "fixture-lesson", "symptom": "s", "description": "d",
                                    "body": "b"}]}, fh)
        self.fixture = self._tree()

    def _tree(self):
        return sorted(os.path.relpath(os.path.join(dp, n), self.home)
                      for dp, dirs, files in os.walk(self.home) for n in dirs + files)

    # -- the shape scan ---------------------------------------------------

    def test_no_shipped_file_defaults_to_a_documents_vault(self):
        found = scan()
        self.assertEqual(found, {}, "these shipped files default to a vault folder under the "
                                    "home Documents folder instead of asking "
                                    "bm_vault.resolve_vault_root: %s" % found)

    def test_the_shape_finds_each_form(self):
        for src in ('V = os.path.expanduser("~/Documents/Sample Vault")\n',
                    'V = os.path.join(HOME, "Documents", "Sample Vault")\n',
                    'V = pathlib.Path.home() / "Documents" / "Sample Vault"\n',
                    'V = "~/Documents/" + "Sample Vault"\n',
                    'ap.add_argument("--x", default="~/Documents/Sample Vault/50-Reference/x.md")\n'):
            with self.subTest(src=src):
                self.assertEqual(documents_vault_sites(src), [1])

    def test_the_shape_ignores_what_is_not_a_vault_default(self):
        for src in ('"""Reads ~/Documents/Sample Vault/AGENTS.md, the constitution."""\n',
                    'R = os.path.expanduser("~/Documents/BrotherModeUp-handovers")\n',
                    'T = os.path.join(HOME, "Documents", "BrotherModeUp", "tools", "bm_vault.py")\n',
                    'V = os.path.join(tmp, "Sample Vault")\n'):
            with self.subTest(src=src):
                self.assertEqual(documents_vault_sites(src), [])

    def test_an_unparseable_shipped_file_is_reported(self):
        os.makedirs(os.path.join(self.home, "scripts"))
        with open(os.path.join(self.home, "scripts", "broken.py"), "w") as fh:
            fh.write("def (:\n")
        self.assertEqual(scan(self.home), {"scripts/broken.py": "unparseable: SyntaxError"})

    def test_pattern_note_copies_are_byte_identical(self):
        blobs = {}
        for rel in COPIES:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                blobs[rel] = fh.read()
        self.assertEqual(len(set(blobs.values())), 1,
                         "pattern_note.py differs between %s" % ", ".join(sorted(blobs)))

    # -- the entry points ---------------------------------------------------

    def test_every_tool_reads_no_data_with_no_setting(self):
        env = scrubbed_env(self.home)
        for argv in ENTRY:
            argv = [a.replace("{home}", self.home) for a in argv]
            with self.subTest(tool=argv[0]):
                rc, out, err = run(argv, env)
                said = out + err
                self.assertEqual(rc, 2, "%s exited %d, want 2 (NO-DATA): %s" % (argv[0], rc, said[-400:]))
                self.assertIn("NO-DATA", said, said[-400:])
                self.assertRegex(said, r"BROTHERMODE_VAULT(_ROOT)?\b", "the line names no setting to fix")
                self.assertNotIn(os.path.join(self.home, "Documents"), said,
                                 "%s reached a folder nobody configured" % argv[0])
                self.assertEqual(self._tree(), self.fixture,
                                 "%s wrote into the home before saying NO-DATA" % argv[0])

    def _lock_check(self, **extra):
        return run([T + "bm_vault_lock.py", "check"], scrubbed_env(self.home, **extra))

    def test_a_configured_vault_is_used(self):
        vault = tempfile.mkdtemp(prefix="key1f-vault-")
        cfg = os.path.join(self.home, ".claude")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "bm_vault.json"), "w") as fh:
            json.dump({"vault": vault}, fh)
        for name in ("BROTHERMODE_VAULT_ROOT", "BM_VAULT_ROOT", "BROTHERMODE_VAULT", None):
            with self.subTest(setting=name or "config"):
                rc, out, err = self._lock_check(**({name: vault} if name else {}))
                self.assertEqual((rc, out.strip()), (0, "NONE"), err)

    def test_two_names_on_two_folders_fail(self):
        a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
        rc, out, err = self._lock_check(BROTHERMODE_VAULT_ROOT=a, BM_VAULT_ROOT=b)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("different folders", err)

    def test_a_line_break_in_a_setting_cannot_forge_a_line(self):
        rc, out, err = self._lock_check(BROTHERMODE_VAULT_ROOT="/a\nPASS: vault ok",
                                        BM_VAULT_ROOT=tempfile.mkdtemp())
        self.assertEqual(rc, 2, out + err)
        lines = [l for l in err.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, err)
        self.assertFalse(any(l.startswith("PASS") for l in lines), err)

    # -- the in process readers ---------------------------------------------

    def _in_process(self, code, **extra):
        return run(["-c", "import sys; sys.path.insert(0, 'scripts'); " + code],
                   scrubbed_env(self.home, **extra))

    def test_brother_run_notice_reads_the_resolver(self):
        """The KEY1.a review case: only BROTHERMODE_VAULT set, a note inside it
        is inside the vault (it used to print Kept in this run)."""
        vault = tempfile.mkdtemp(prefix="key1f-vault-")
        code = ("import brother_run as b; print(b._inside_vault(%r), b._vault_root())"
                % os.path.join(vault, "note.md"))
        rc, out, err = self._in_process(code, BROTHERMODE_VAULT=vault)
        self.assertEqual((rc, out.split()), (0, ["True", "None"]), err)
        rc, out, err = self._in_process(code)
        self.assertEqual((rc, out.split()), (0, ["False", "None"]), err)

    def test_attempt_hook_research_reads_no_guessed_folder(self):
        """attempt_hook read find_out's old VAULT constant at import; with no
        setting its vault sources answer nothing, and a 40-Failures folder in
        the working directory is never read in place of a vault."""
        cwd = tempfile.mkdtemp(prefix="key1f-cwd-")
        os.makedirs(os.path.join(cwd, "40-Failures"))
        with open(os.path.join(cwd, "40-Failures", "decoy.md"), "w") as fh:
            fh.write("---\ntitle: zebrafish decoy\n---\nzebrafish zebrafish\n")
        for where in (HERE, BM_TOOLS):  # both copies ship
            with self.subTest(copy=where):
                code = ("import sys; sys.path.insert(0, %r); import attempt_hook as a; "
                        "print(a.__file__); print(a._find_out_sources('zebrafish')[0][1]())" % where)
                rc, out, err = run(["-c", code], scrubbed_env(self.home), cwd=cwd)
                lines = out.split()
                self.assertEqual((rc, lines[0]), (0, os.path.join(where, "attempt_hook.py")), err)
                self.assertEqual(lines[1:], ["None"], err)

    def test_board_counter_reads_no_data(self):
        rc, out, err = self._in_process(
            "import board_status as b; c, _cmd, why = b.notes_written_this_week(); print(c); print(why)")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines()[0], "None")
        self.assertIn("BROTHERMODE_VAULT", out)

    def test_record_drift_adds_only_the_configured_vault(self):
        vault = tempfile.mkdtemp(prefix="key1f-vault-")
        code = "import json, record_drift as r; print(json.dumps(r.KNOWN_REPOS))"
        rc, out, err = self._in_process(code, BROTHERMODE_VAULT_ROOT=vault)
        self.assertEqual(rc, 0, err)
        self.assertIn(vault, json.loads(out))
        rc, out, err = self._in_process(code)
        self.assertEqual(rc, 0, err)
        self.assertFalse([r for r in json.loads(out) if VAULT_WORD.search(os.path.basename(r))], out)


if __name__ == "__main__":
    unittest.main()
