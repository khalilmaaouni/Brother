#!/usr/bin/env python3
"""l5c_audit: inventory, deterministic sample and hash lock for the L5c
test integrity audit (sub unit L5c.2).

Why this exists. The audit's own recorded failure class is a mutation seam
that accepts any id, which turns a mistyped mutation test into a tautology.
The cure, before any mutant runs, is a pre-registered sample: the exact
files and the exact test methods, frozen with the sha256 of both the test
file and the source it guards, chosen by a rule that cannot depend on the
order the filesystem happened to hand the tree back.

Inventory is AST only. Importing a suite under audit would run its module
level code against the live tree, which is exactly what a probe must never
do from inside the audit. A file that cannot be read as Python raises: an
inventory that quietly returned "no tests" for an unreadable file would
shrink the denominator and inflate the score, the hollow pass this audit
exists to catch.

A category with nothing to draw from is NO-DATA (NoData, a ValueError, so
every caller that already refuses a bad input by catching ValueError
refuses this too) and BLOCKS. A missing --vault-root, a missing
--vault-token, an empty vault or a token that matches nothing are all
NO-DATA, never an empty pass.

Python 3.8+, standard library only. No network, and the tree being
audited is never imported.
"""

import ast
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

#: This module's own location, and the repository root it sits in. Both are
#: derived from __file__, never from the working directory: a run started from
#: anywhere still resolves a repository-relative row against the same tree
#: this module lives in.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

__all__ = [
    "NoData",
    "DISPATCH_TEST",
    "HOOK_TEST",
    "SAMPLES_PER_CATEGORY",
    "PENDING_ANCHOR",
    "PROBE_RUNNER",
    "sha256_file",
    "collect_test_methods",
    "discover_test_files",
    "select_samples",
    "load_samples",
    "validate_sample",
    "verify_anchor",
    "manifest_for",
    "run_probe",
    "CATEGORIES",
    "BAR_A",
    "BAR_B",
    "mark_measured",
    "attribute",
    "score",
]


#: The two suites whose paths are fixed by the specification. Everything
#: else is drawn from the vault walk, whose paths the operator supplies.
DISPATCH_TEST = os.path.join("plugin", "runtime", "brother", "core",
                             "test_openrouter_dispatch.py")
HOOK_TEST = os.path.join("scripts", "test_brother_antigravity_hook.py")

#: (category, files to take, methods per file), in output order. Vault is
#: two files by two methods, dispatch and hook are their single fixed
#: suite by three. Files are the first sorted paths, methods the first
#: sorted names, so the same tree always yields the same rows.
SAMPLES_PER_CATEGORY = (
    ("vault", 2, 2),
    ("dispatch", 1, 3),
    ("hook", 1, 3),
)


class NoData(ValueError):
    """An unmeasurable category or a missing input.

    It is a ValueError so a caller that already refuses a bad input by
    catching ValueError refuses this too. NO-DATA is never a pass and
    never an empty result set: the audit BLOCKS.
    """


def _require_text(value, name):
    """The one validation every string parameter routes through.

    A non-string (None, a number, a bool, bytes, NaN) or an empty string
    is refused here, by name, rather than handed to open() or os.walk()
    where it would raise a bare interpreter TypeError.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string" % name)
    return value


def sha256_file(path: str) -> str:
    """The sha256 of the file's BYTES.

    Files are read as bytes, never as decoded text: a hash lock that
    normalised line endings or re-encoded would stop being a lock on the
    file actually audited. A missing, unreadable or non-file path is
    refused, never a constant digest that would silently match another
    row.
    """
    _require_text(path, "path")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError("cannot read %r: %s" % (path, exc))
    return hashlib.sha256(data).hexdigest()


def collect_test_methods(path: str) -> List[str]:
    """Every test_ function name in the file, sorted, read by ast only.

    Module level and top level class bodies are both walked: the estate
    mixes both shapes and a census that silently missed one shape would
    undercount the file's own denominator. The file is never imported,
    because importing a suite under audit would run its module level code
    against the live tree. A file that does not parse raises ValueError,
    never []: [] reads as "a suite with no tests", which is the one
    answer that must never be invented.
    """
    _require_text(path, "path")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("cannot read %r: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("%r is not utf-8: %s" % (path, exc))
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise ValueError("cannot parse %r: %s" % (path, exc))
    methods = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            methods.append(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name.startswith("test_"):
                    methods.append(child.name)
    return sorted(methods)


def _vault_files(vault_root: str, vault_token: str) -> List[str]:
    """Every test_*.py under vault_root whose absolute path contains the
    token.

    The operator supplies both --vault-root and --vault-token; the real
    vault paths are deliberately not hard coded here, so an unset variable
    is NO-DATA rather than a silent guess. Zero matches is NO-DATA for the
    same reason: an empty category would drop four rows from the
    denominator and inflate the score.
    """
    if not isinstance(vault_root, str) or not vault_root:
        raise NoData("vault search root is missing: pass --vault-root")
    if not isinstance(vault_token, str) or not vault_token:
        raise NoData("vault search token is missing: pass --vault-token")
    found = []
    for dirpath, _dirnames, filenames in os.walk(vault_root):
        for name in filenames:
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            full = os.path.abspath(os.path.join(dirpath, name))
            if vault_token in full:
                found.append(full)
    found = sorted(set(found))
    if not found:
        raise NoData("vault category matched no test_*.py under %r for token %r" % (vault_root, vault_token))
    return found


def discover_test_files(root: str, vault_root: Optional[str],
                        vault_token: Optional[str]) -> List[str]:
    """Every file the audit will draw a sample from, as absolute paths.

    That is the two fixed suites (dispatch and hook) plus every vault
    test_*.py whose path contains the token. Paths are de-duplicated and
    sorted so the same tree yields the same list on a second run, however
    the walk happened to enumerate it. A missing fixed suite or an
    unmeasurable vault category is NO-DATA: the audit BLOCKS, it never
    silently shrinks to whatever happened to be found.
    """
    _require_text(root, "root")
    files = []
    for rel in (DISPATCH_TEST, HOOK_TEST):
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            raise NoData("fixed suite is missing: %s" % full)
        files.append(os.path.abspath(full))
    files.extend(_vault_files(vault_root, vault_token))
    return sorted(set(files))


def _sibling_source(test_path: str) -> str:
    """The module a suite is a test OF: the sibling with the leading
    test_ stripped, when that sibling exists.

    For a suite whose source cannot be named from its own file name (the
    vault facade suites are one such case) the answer is the empty string
    and the row's source lock is empty, which is VISIBLE, never a
    plausible looking guess.
    """
    directory = os.path.dirname(test_path)
    base = os.path.basename(test_path)
    if not base.startswith("test_") or not base.endswith(".py"):
        return ""
    candidate = os.path.join(directory, base[len("test_"):])
    if os.path.isfile(candidate):
        return candidate
    return ""


def _sample_row(category: str, test_path: str, method: str,
                total: int) -> Dict[str, Any]:
    src = _sibling_source(test_path)
    return {
        "category": category,
        "test": test_path,
        "test_sha256": sha256_file(test_path),
        "method": method,
        "src": src,
        "src_sha256": sha256_file(src) if src else "",
        "sampled_of": total,
    }


def _rows_for(category: str, files: List[str], files_wanted: int,
              per_file: int) -> List[Dict[str, Any]]:
    """The category's quota of files_wanted * per_file rows: at most per_file
    methods from each sorted file, and a file holding fewer methods than
    per_file hands its unused share to the next sorted file.

    Measured 2026-09-30: every vault facade suite holds ONE test, so taking
    exactly files_wanted files yielded 2 vault rows where the quota is 4, and
    a whole sample of 8 that the gate's sampled_at_least_ten refuses forever.
    The same tree still yields the same rows, and a tree whose files all hold
    per_file or more methods yields exactly the rows it did before.
    """
    quota = files_wanted * per_file
    rows = []
    for test_path in files:
        if len(rows) >= quota:
            break
        methods = collect_test_methods(test_path)
        total = len(methods)
        for method in methods[:min(per_file, quota - len(rows))]:
            rows.append(_sample_row(category, test_path, method, total))
    return rows


def select_samples(root: str, vault_root: Optional[str],
                   vault_token: Optional[str]) -> List[Dict[str, Any]]:
    """The pre-registered sample: vault 4 (two files by two methods),
    dispatch 3 and hook 3.

    Rows are chosen by sorted file path then sorted method name, so the
    same tree yields the same rows on a second run and a survivor cannot
    be argued away by choosing a different test after the fact. Every row
    carries the sha256 of the test file and of the source it guards at
    the moment it was chosen; an edit after that invalidates the lock and
    the audit must re-inventory rather than reuse the stale row.
    """
    _require_text(root, "root")
    files = discover_test_files(root, vault_root, vault_token)
    dispatch_abs = os.path.abspath(os.path.join(root, DISPATCH_TEST))
    hook_abs = os.path.abspath(os.path.join(root, HOOK_TEST))
    buckets: Dict[str, List[str]] = {"vault": [], "dispatch": [], "hook": []}
    for path in files:
        absolute = os.path.abspath(path)
        if absolute == dispatch_abs:
            buckets["dispatch"].append(path)
        elif absolute == hook_abs:
            buckets["hook"].append(path)
        else:
            buckets["vault"].append(path)
    rows = []
    for category, files_wanted, per_file in SAMPLES_PER_CATEGORY:
        rows.extend(_rows_for(category, buckets[category], files_wanted, per_file))
    return rows


# ---------------------------------------------------------------------------
# L5c.3: manifest, anchor validity and scratch-only execution.
#
# Everything below is ADDITIVE. The inventory, the deterministic sample and
# the hash lock above keep their exact names, defaults and outputs, so every
# existing caller is untouched.
#
# Two deliberate properties, both forced by this unit's own safety screen:
#
#   * scripts/mutation_gate.py holds a command runner, so this module never
#     imports it. The one behaviour this section needed from it, a
#     unique-anchor text patch, is written here as _unique_anchor_patch with
#     the same contract: a zero-hit anchor and a multiple-hit anchor are both
#     refused, never guessed at. The property both guard is identical, and it
#     is this audit's own recorded failure class: a mutation seam that accepts
#     any anchor lets a mistyped mutant read as a tested one.
#   * The probe is a command runner too, so this module never imports it and
#     never starts a child process. run_probe hands the probe's own argument
#     list to the callable the caller installs in PROBE_RUNNER; with no
#     callable installed the group is NO-DATA, never a pass.
# ---------------------------------------------------------------------------

#: The literal a pre-registered row carries until its own inventory lands. A
#: row still carrying it refuses to run (NO-DATA): guessing an anchor would
#: register a mutant nobody resolved.
PENDING_ANCHOR = "PENDING-INVENTORY"

#: This process's probe runner, or None. The caller installs a callable that
#: receives the probe's own argument list and returns the probe's own exit
#: code: 0 every valid mutant killed, 1 at least one survivor, 2 nothing
#: measurable (NO-DATA).
PROBE_RUNNER = None


def _unique_anchor_patch(text: str, old: str, new: str) -> Tuple[Optional[str], str]:
    """(patched_text, problem), problem "" on success.

    The anchor must occur exactly once. Zero hits and multiple hits are both
    refused rather than guessed at: a patch that landed on the wrong
    occurrence, or on nothing at all, would still report a mutant as tested
    while the seam it claimed to break was never broken.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string, got %s" % type(text).__name__)
    if not isinstance(old, str) or not old:
        raise ValueError("old must be a non-empty string")
    if not isinstance(new, str):
        raise ValueError("new must be a string")
    hits = text.count(old)
    if hits == 0:
        return None, "anchor text not found (the seam moved or was refactored away)"
    if hits > 1:
        return None, "anchor text is not unique (%d occurrences)" % hits
    return text.replace(old, new, 1), ""


def _under_root(root: str, rel: str) -> str:
    """root joined with a repository-relative path, or ValueError.

    An absolute path, or anything that walks out of root, is refused: a row
    that names a file elsewhere is corrupt input, never a file to read. Both
    sides are resolved before the comparison, so a tree reached through a
    symlink (a temp folder on this host) is not refused by accident.
    """
    _require_text(root, "root")
    _require_text(rel, "path")
    if os.path.isabs(rel):
        raise ValueError("absolute path refused: %s" % rel)
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep) or (os.sep + ".." + os.sep) in norm:
        raise ValueError("escape refused: %s" % rel)
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(real_root, rel))
    if full != real_root and not full.startswith(real_root + os.sep):
        raise ValueError("outside root refused: %s" % rel)
    return full


def _hash_drift(sample: Dict[str, Any], root: str) -> List[str]:
    """A hash recorded in the row that no longer matches the file on disk.

    The pre-registration is a lock only while the bytes still agree: a source
    or test edited after the row was registered invalidates the row, and the
    honest answer is to re-inventory, never to run a mutant against a file the
    row was never checked against. A row that records no hash is not drift
    here: run_probe hashes the source before and after the probe.
    """
    problems = []
    for key, path_key in (("src_sha256", "src_path"), ("test_sha256", "test_path")):
        recorded = sample.get(key)
        if not isinstance(recorded, str) or not recorded:
            continue
        rel = sample.get(path_key)
        if not isinstance(rel, str) or not rel:
            problems.append("%s is recorded but %s is not a path" % (key, path_key))
            continue
        try:
            actual = sha256_file(_under_root(root, rel))
        except ValueError as exc:
            problems.append("hash drift check failed for %s: %s" % (path_key, exc))
            continue
        if actual != recorded:
            problems.append("hash drift: %s recorded %s for %s but the file now "
                            "hashes to %s" % (key, recorded, rel, actual))
    return problems


def load_samples(path: str) -> List[Dict[str, Any]]:
    """The pre-registered sample rows, read as BYTES and parsed as a JSON
    array of objects.

    A missing, unreadable, non utf-8, non array, non object or empty file is
    NO-DATA (NoData, a ValueError, so every caller that already refuses a bad
    input by catching ValueError refuses this too). An empty sample is not a
    smaller audit, it is no audit: a run whose denominator shrank cannot meet
    the gate, so it blocks.
    """
    _require_text(path, "path")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise NoData("cannot read samples %r: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NoData("samples %r is not utf-8: %s" % (path, exc))
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise NoData("samples %r is not valid JSON: %s" % (path, exc))
    if not isinstance(parsed, list):
        raise NoData("samples %r must hold a JSON array, found %s"
                     % (path, type(parsed).__name__))
    rows = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise NoData("samples %r row %d is not an object, found %s"
                         % (path, index, type(item).__name__))
        rows.append(item)
    if not rows:
        raise NoData("samples %r holds no rows; an empty sample is NO-DATA, "
                     "never a pass" % path)
    return rows


def validate_sample(sample: Dict[str, Any], root: str) -> List[str]:
    """Every reason this pre-registered row may not be run, as text.

    An empty list means the row is well formed. It says nothing about whether
    the anchor resolves: verify_anchor owns that. A row that would mutate its
    own test is refused here, by name, because this audit never edits a test
    (L5C3-REQ-1).

    A sample that is not an object, or whose paths are not non-empty strings,
    is a RETURNED refusal (this list), never a TypeError: a caller that only
    reads the list can never mistake a hostile row for a valid one, and a
    wrong type is never silently accepted. root is a session parameter, not
    row data, so a bad root raises ValueError, the same deliberate error the
    rest of this module already raises.
    """
    _require_text(root, "root")
    if not isinstance(sample, dict):
        return ["sample must be an object, found %s" % type(sample).__name__]
    problems = []
    src_path = sample.get("src_path")
    test_path = sample.get("test_path")
    for name, value in (("src_path", src_path), ("test_path", test_path)):
        if not isinstance(value, str) or not value:
            problems.append("%s must be a non-empty string, found %r" % (name, value))
    if isinstance(src_path, str) and src_path and isinstance(test_path, str) and test_path:
        if src_path == test_path or os.path.basename(src_path).startswith("test_"):
            problems.append("mutating the test: src_path %r is a test file, and "
                            "this audit never edits a test" % src_path)
    problems.extend(_hash_drift(sample, root))
    return problems


def verify_anchor(root: str, sample: Dict[str, Any]) -> List[str]:
    """Every reason this row's anchor may not be used, as text.

    The anchor is resolved against the real source file, read as BYTES, with
    the one unique-hit text patch this module owns (_unique_anchor_patch: a
    zero-hit or multiple-hit anchor is refused, never guessed). A row whose
    anchor is still PENDING-INVENTORY is NO-DATA by name, never a guess
    (L5C3-REQ-3). A patched file that does not parse is refused before any
    probe runs, so a mutant that is a syntax error can never be counted as a
    kill.
    """
    _require_text(root, "root")
    if not isinstance(sample, dict):
        return ["sample must be an object, found %s" % type(sample).__name__]
    src_path = sample.get("src_path")
    if not isinstance(src_path, str) or not src_path:
        return ["NO-DATA: src_path must be a non-empty string, found %r" % (src_path,)]
    if src_path == PENDING_ANCHOR:
        return ["NO-DATA: src_path is %s; the source file this row guards is "
                "not inventoried yet" % PENDING_ANCHOR]
    old = sample.get("old")
    if not isinstance(old, str) or not old:
        return ["NO-DATA: old must be a non-empty anchor string, found %r" % (old,)]
    if old == PENDING_ANCHOR:
        return ["NO-DATA: old is %s; the anchor has not been inventoried, and "
                "guessing one is forbidden" % PENDING_ANCHOR]
    new = sample.get("new")
    if not isinstance(new, str):
        return ["new must be a string, found %s" % type(new).__name__]
    if new == PENDING_ANCHOR:
        return ["NO-DATA: new is %s; the mutant has not been inventoried"
                % PENDING_ANCHOR]
    try:
        full = _under_root(root, src_path)
    except ValueError as exc:
        return ["%s" % exc]
    try:
        with open(full, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return ["NO-DATA: cannot read %s: %s" % (full, exc)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ["NO-DATA: %s is not utf-8: %s" % (full, exc)]
    patched, problem = _unique_anchor_patch(text, old, new)
    if problem or patched is None:
        return ["anchor unresolved in %s: %s" % (src_path, problem or "no patched text")]
    try:
        ast.parse(patched, filename=src_path)
    except SyntaxError as exc:
        return ["the mutant does not parse: %s" % exc]
    return []


def manifest_for(group: List[Dict[str, Any]], out_dir: str) -> str:
    """The mutant manifest for ONE (src_path, test_path) group, and its path.

    One probe call per group means one manifest per group, so every row in the
    group must name the same source and the same test file: a mixed group is
    corrupt input, never something to split silently (L5C3-REQ-4). A row whose
    anchor is still PENDING-INVENTORY, a row with no id, a duplicate id and a
    row with no expected red token all refuse to be staged (NO-DATA). The
    estate's own recorded failure class is a mutation seam that accepts any id,
    which turns a mistyped mutation test into a tautology, so this writer never
    invents and never reuses one.

    The shape written is the probe loader's own contract: a JSON array of
    objects with id, why, old, new, expect_test and the file each mutant
    belongs to, written as bytes, under a name derived from the group so the
    same group always lands in the same file.
    """
    _require_text(out_dir, "out_dir")
    if not isinstance(group, (list, tuple)):
        raise ValueError("group must be a list of sample rows, found %s"
                         % type(group).__name__)
    if not group:
        raise NoData("group holds no sample rows; an empty group is NO-DATA, "
                     "never a run that tested nothing")
    src_path = None
    test_path = None
    seen_ids = set()
    entries = []
    for index, row in enumerate(group):
        if not isinstance(row, dict):
            raise NoData("group row %d is not an object, found %s"
                         % (index, type(row).__name__))
        row_src = row.get("src_path")
        row_test = row.get("test_path")
        for name, value in (("src_path", row_src), ("test_path", row_test)):
            if not isinstance(value, str) or not value:
                raise NoData("group row %d: %s must be a non-empty string"
                             % (index, name))
        if src_path is None:
            src_path = row_src
            test_path = row_test
        elif (row_src, row_test) != (src_path, test_path):
            raise NoData("group mixes %r/%r with %r/%r; group by "
                         "(src_path, test_path) before staging a run"
                         % (src_path, test_path, row_src, row_test))
        mutant_id = row.get("id")
        if not isinstance(mutant_id, str) or not mutant_id:
            raise NoData("group row %d: id must be a non-empty string; a seam "
                         "that accepts any id is the tautology this audit "
                         "exists to catch" % index)
        if mutant_id in seen_ids:
            raise NoData("group row %d: duplicate mutant id %r"
                         % (index, mutant_id))
        seen_ids.add(mutant_id)
        old = row.get("old")
        if not isinstance(old, str) or not old or old == PENDING_ANCHOR:
            raise NoData("group row %d: anchor is %r; NO-DATA, refusing to "
                         "stage a guessed anchor" % (index, old))
        new = row.get("new")
        if not isinstance(new, str) or new == PENDING_ANCHOR:
            raise NoData("group row %d: new must be an inventoried string, "
                         "found %r" % (index, new))
        expect = row.get("expect_test")
        if not isinstance(expect, str) or not expect:
            raise NoData("group row %d: expect_test must be a non-empty "
                         "string" % index)
        why = row.get("why")
        if not isinstance(why, str) or not why:
            why = "L5c.3 pre-registered mutant %s" % mutant_id
        entries.append({"id": mutant_id, "why": why, "old": old,
                        "new": new, "expect_test": expect, "file": src_path})
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise ValueError("cannot create the manifest folder %r: %s" % (out_dir, exc))
    digest = hashlib.sha256(("%s|%s" % (src_path, test_path)).encode("utf-8")).hexdigest()[:16]
    manifest_path = os.path.join(out_dir, "mutants-%s.json" % digest)
    payload = json.dumps(entries, indent=1, sort_keys=True) + "\n"
    try:
        with open(manifest_path, "wb") as handle:
            handle.write(payload.encode("utf-8"))
    except OSError as exc:
        raise ValueError("cannot write the manifest %r: %s" % (manifest_path, exc))
    return os.path.abspath(manifest_path)


def _locate(path: str) -> str:
    """The file a path names: an absolute path is used as it is, and a
    repository-relative path is resolved against the repository root this
    module lives in, the same base the probe's own paths use."""
    _require_text(path, "path")
    return path if os.path.isabs(path) else os.path.join(_REPO_ROOT, path)


def run_probe(src: str, test: str, manifest: str, copy_paths: List[str],
              timeout: int, out_json: str) -> Dict[str, Any]:
    """Run the probe for ONE (src, test) group and record what happened.

    This function never reports a pass by default. The probe script is a
    command runner, so this module never imports it and never starts a child
    process of its own: the callable installed in PROBE_RUNNER is the caller's
    own command runner. It is handed the probe's own argument list, which
    already carries --copy for src, test and every extra path the group needs
    (L5C3-REQ-4), and it returns the probe's own exit code.

    Only exit 0 and exit 1 are a measurement. Exit 2, a runner that is absent
    or returns anything other than an integer, a missing --out file, a --out
    file that is not an object, and a source whose own bytes changed during
    the run are all NO-DATA (L5C3-REQ-5): an unmeasured group blocks, it is
    never scored.
    """
    _require_text(src, "src")
    _require_text(test, "test")
    _require_text(manifest, "manifest")
    _require_text(out_json, "out_json")
    if not isinstance(copy_paths, (list, tuple)):
        raise ValueError("copy_paths must be a list of paths, found %s"
                         % type(copy_paths).__name__)
    for item in copy_paths:
        if not isinstance(item, str) or not item:
            raise ValueError("copy_paths must hold non-empty strings, found %r"
                             % (item,))
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout must be a positive integer, found %r" % (timeout,))

    copies = []
    for path in (src, test) + tuple(copy_paths):
        if path not in copies:
            copies.append(path)
    argv = ["--src", src, "--test", test, "--mutants", manifest,
            "--out", out_json, "--timeout", str(timeout)]
    for path in copies:
        argv.extend(["--copy", path])

    result = {"src": src, "test": test, "manifest": manifest, "out": out_json,
              "argv": argv, "exit": None, "status": "NO-DATA", "summary": None,
              "detail": "", "src_sha256_before": "", "src_sha256_after": ""}

    located = _locate(src)
    try:
        result["src_sha256_before"] = sha256_file(located)
    except ValueError as exc:
        result["detail"] = "NO-DATA: cannot hash the source before the run: %s" % exc
        return result

    probe_runner = PROBE_RUNNER
    if not callable(probe_runner):
        result["detail"] = ("NO-DATA: no probe runner is installed in this "
                            "process (l5c_audit.PROBE_RUNNER is not callable); "
                            "refusing rather than reporting an unmeasured group "
                            "as a pass")
        return result
    try:
        code = probe_runner(argv)
    except Exception as exc:  # noqa: BLE001 (a runner's own failure surface is not this row's concern; any failure is NO-DATA)
        result["detail"] = "NO-DATA: the probe runner raised %s" % type(exc).__name__
        return result
    if isinstance(code, bool) or not isinstance(code, int):
        result["detail"] = ("NO-DATA: the probe runner returned %r, which is "
                            "not an exit code" % (code,))
        return result
    result["exit"] = code
    if code not in (0, 1):
        result["detail"] = ("NO-DATA: probe exit %d is unmeasured (0 is every "
                            "valid mutant killed, 1 is a survivor)" % code)
        return result
    try:
        with open(out_json, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        result["detail"] = ("NO-DATA: the probe wrote no --out file (%s); an "
                            "unwritten measurement is not a pass" % exc)
        return result
    try:
        summary = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        result["detail"] = "NO-DATA: the --out file could not be read: %s" % exc
        return result
    if not isinstance(summary, dict):
        result["detail"] = ("NO-DATA: the --out file must hold an object, found %s"
                            % type(summary).__name__)
        return result
    try:
        result["src_sha256_after"] = sha256_file(located)
    except ValueError as exc:
        result["detail"] = "NO-DATA: cannot hash the source after the run: %s" % exc
        return result
    if result["src_sha256_before"] != result["src_sha256_after"]:
        result["detail"] = ("NO-DATA: hash drift on %s (%s then %s); "
                            "re-inventory before scoring"
                            % (src, result["src_sha256_before"],
                               result["src_sha256_after"]))
        return result
    result["summary"] = summary
    result["status"] = "MEASURED"
    result["detail"] = "probe exit %d" % code
    return result


# ---------------------------------------------------------------------------
# L5c.4: attribution, the sampled score gate and the mock-only floor.
#
# Everything below is ADDITIVE. The inventory, the pre-registered sample, the
# hash lock, the manifest and the probe envelope above keep their exact names,
# defaults, outputs and statuses, so every existing caller is untouched.
#
# The estate's own recorded failure classes are answered here by name:
#
#   * "a mutation seam that accepts any id makes a mistyped mutation test a
#     tautology": a kill is credited only when the probe said KILLED for THIS
#     sample's id AND the sample's pre-registered method AND its pre-registered
#     red token both appear in the captured tail. An unrelated red is reported
#     by name as UNATTRIBUTED and is never a kill.
#   * "a mocked tool call proves handling, not what the tool matches": the gate
#     carries a mock-only floor, state_backed_killed >= 2, so a sample killed
#     only by mock-shaped assertions cannot pass.
#
# The gate is an integer comparison against the estate's own bar of 17 of 20.
# The rounded score is reported and never consulted.
# ---------------------------------------------------------------------------

#: The estate's bar, 17 of 20, as two integers so the rate test is exact:
#: killed * BAR_A >= BAR_B * total. Never 8.5: a float bar is where a rounded
#: comparison turns a survivor into a pass.
BAR_A, BAR_B = 20, 17

#: The three pre-registered categories. A run with no row for one of them has
#: not measured the sample, however many of its rows were killed.
CATEGORIES = ("vault", "dispatch", "hook")

#: The statuses that are a real measurement of a sample row: the suite ran and
#: this row got an answer. NO-DATA, INVALID and TIMEOUT are deliberately
#: absent, and any status not listed here (an unknown one included) is not
#: measured: it blocks the gate, it is never a smaller denominator.
MEASURED_STATUSES = ("KILLED", "SURVIVED", "UNATTRIBUTED", "INFRA")

#: The probe's own per-mutant verdicts, the only results attribute() maps. A
#: result outside this tuple is NO-DATA: an unknown verdict is never read as a
#: survivor, and never as a kill.
PROBE_VERDICTS = ("KILLED", "SURVIVED", "INVALID", "TIMEOUT", "INFRA")


def _require_object(value, name):
    """The one object check every public function below routes through.

    A non-object (None, a number, a bool, bytes, a string, NaN, a list) is
    refused here, by name, rather than reaching a .get() that would raise a
    bare interpreter AttributeError or TypeError.
    """
    if not isinstance(value, dict):
        raise ValueError("%s must be an object, found %s"
                         % (name, type(value).__name__))
    return value


def _sample_field(sample: Dict[str, Any], name: str) -> Optional[str]:
    """A required non-empty string field of a pre-registered sample row, or
    None when it is missing or unusable.

    None is the caller's signal to refuse the row as NO-DATA by name. It is
    never filled in with a guess: a sample that does not say which method and
    which red token it registered cannot have its kill attributed.
    """
    value = sample.get(name)
    if not isinstance(value, str) or not value:
        return None
    return value


def _probe_payload(summary: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """(the probe summary object, problem), problem "" on success.

    summary is either the envelope run_probe returns (status MEASURED, with
    the probe's own summary under "summary") or the probe's own summary (an
    object with a rows list). Anything else is NO-DATA by name: a measurement
    that cannot be read is never scored as a pass.
    """
    envelope_status = summary.get("status")
    if isinstance(envelope_status, str):
        if envelope_status != "MEASURED":
            detail = summary.get("detail")
            return None, ("the probe envelope is %s (%s)"
                          % (envelope_status,
                             detail if isinstance(detail, str) else ""))
        payload = summary.get("summary")
        if not isinstance(payload, dict):
            return None, ("the probe envelope is MEASURED but carries no "
                          "summary object")
    else:
        payload = summary
    if not isinstance(payload.get("rows"), list):
        return None, "the probe summary carries no rows list"
    return payload, ""


def mark_measured(row: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of row with measured set from the row's own status.

    A row is measured only when the suite ran and got an answer for it:
    KILLED, SURVIVED, UNATTRIBUTED or INFRA. NO-DATA, INVALID, TIMEOUT and any
    status this module does not know are unmeasured, and an unmeasured row is
    never promoted: the audit scores only what the probe measured.

    A row that is not an object, or whose status is missing, empty or not a
    string, is corrupt input and raises. Reading it as measured False would let
    a caller that never ran the sample score it, and reading it as anything
    else would report a rate for a run that never happened.
    """
    _require_object(row, "row")
    status = row.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("row status must be a non-empty string, found %r"
                         % (status,))
    marked = dict(row)
    marked["measured"] = status in MEASURED_STATUSES
    return marked


def attribute(sample: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    """Attribute ONE pre-registered sample against ONE probe summary.

    A kill is credited only when the probe's own row for this sample's id says
    KILLED AND the sample's pre-registered method AND its pre-registered red
    token both appear in the captured tail. Every other red is reported by
    name as UNATTRIBUTED, which is measured and is never a kill; INVALID,
    TIMEOUT and a missing or unreadable measurement are NO-DATA, which blocks.

    The sample carries id, category, method and red_token, all non-empty
    strings, and an optional boolean state_backed (absent is False, the strict
    direction: an unflagged row never counts toward the mock-only floor).
    summary is either the object run_probe returns or the probe's own summary.

    A sample or summary that is not an object, or a summary holding two rows
    for one mutant id, is corrupt input and raises: two counts that cannot all
    be true are never resolved by picking one.
    """
    _require_object(sample, "sample")
    _require_object(summary, "summary")
    state_backed = sample.get("state_backed", False)
    if not isinstance(state_backed, bool):
        raise ValueError("sample state_backed must be a boolean, found %r"
                         % (state_backed,))
    missing = [name for name in ("id", "category", "method", "red_token")
               if _sample_field(sample, name) is None]
    row = {"id": _sample_field(sample, "id"),
           "category": _sample_field(sample, "category"),
           "method": _sample_field(sample, "method"),
           "state_backed": state_backed,
           "status": "NO-DATA",
           "attributed": False,
           "measured": False,
           "detail": ""}
    if missing:
        row["detail"] = ("NO-DATA: the pre-registered sample carries no %s; a "
                         "row that does not say what it registered is never "
                         "attributed" % ", ".join(missing))
        return mark_measured(row)
    payload, problem = _probe_payload(summary)
    if problem:
        row["detail"] = "NO-DATA: %s" % problem
        return mark_measured(row)
    matches = [candidate for candidate in payload["rows"]
               if isinstance(candidate, dict)
               and candidate.get("id") == row["id"]]
    if not matches:
        row["detail"] = ("NO-DATA: the probe summary holds no row for mutant "
                         "%r" % (row["id"],))
        return mark_measured(row)
    if len(matches) > 1:
        raise ValueError("corrupt probe summary: %d rows for mutant id %r"
                         % (len(matches), row["id"]))
    verdict = matches[0].get("result")
    if not isinstance(verdict, str) or verdict not in PROBE_VERDICTS:
        row["detail"] = ("NO-DATA: probe result %r is not one of this module's "
                         "verdicts; an unknown verdict is never a survivor"
                         % (verdict,))
        return mark_measured(row)
    if verdict == "SURVIVED":
        row["status"] = "SURVIVED"
        row["detail"] = "SURVIVED: the suite stayed green under the mutant"
        return mark_measured(row)
    if verdict in ("INVALID", "TIMEOUT"):
        row["status"] = verdict
        row["detail"] = ("NO-DATA: probe result %s; the sample was never "
                         "measured" % verdict)
        return mark_measured(row)
    if verdict == "INFRA":
        row["status"] = "INFRA"
        row["detail"] = ("INFRA: the suite went red on something other than "
                         "the pre-registered test; never a kill")
        return mark_measured(row)
    tail = matches[0].get("output_tail")
    if not isinstance(tail, str) or not tail:
        row["detail"] = ("NO-DATA: the probe reports KILLED but captured no "
                         "tail to attribute it against")
        return mark_measured(row)
    absent = []
    for name, value in (("method", row["method"]),
                        ("red_token", _sample_field(sample, "red_token"))):
        if value not in tail:
            absent.append(name)
    if absent:
        row["status"] = "UNATTRIBUTED"
        row["detail"] = ("the probe went red on %r, but its captured tail "
                         "carries no pre-registered %s: an unrelated red is "
                         "never a kill" % (row["id"], " and ".join(absent)))
        return mark_measured(row)
    row["status"] = "KILLED"
    row["attributed"] = True
    row["detail"] = ("KILLED and attributed: %s and the pre-registered red "
                     "token both appear in the captured tail" % row["method"])
    return mark_measured(row)


def score(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The sampled score, its five conditions and the gate.

    rows is a list of attributed rows: attribute's own output, one per
    pre-registered sample. killed counts ATTRIBUTED kills only, so a red the
    sample never registered cannot lift the rate. total is the whole sampled
    denominator, and measured counts the rows the probe really measured, so a
    NO-DATA, INVALID or TIMEOUT row blocks rather than shrinking the sample.

    Every row must carry its own status, an explicit attributed boolean and a
    category. A row that is not an object, or whose status, attributed flag,
    category or state_backed flag is missing or of the wrong type, is corrupt
    input and raises: the counts cannot all be true, and a scorer that read a
    missing flag as False would report a rate for a sample it never saw.

    score_10 is REPORTED ONLY. The gate is the integer comparison killed *
    BAR_A >= BAR_B * total and the other four conditions, never the rounded
    number: 849 of 1000 rounds to 8.5 and is still below the bar.
    """
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list of attributed sample rows, "
                         "found %s" % type(rows).__name__)
    seen = set()
    marked = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("rows[%d] must be an object, found %s"
                             % (index, type(row).__name__))
        attributed = row.get("attributed")
        if not isinstance(attributed, bool):
            raise ValueError("rows[%d]: attributed must be a boolean, found %r"
                             % (index, attributed))
        category = row.get("category")
        if not isinstance(category, str) or not category:
            raise ValueError("rows[%d]: category must be a non-empty string, "
                             "found %r" % (index, category))
        state_backed = row.get("state_backed", False)
        if not isinstance(state_backed, bool):
            raise ValueError("rows[%d]: state_backed must be a boolean, "
                             "found %r" % (index, state_backed))
        marked_row = mark_measured(row)
        marked_row["attributed"] = attributed
        marked_row["category"] = category
        marked_row["state_backed"] = state_backed
        seen.add(category)
        marked.append(marked_row)
    total = len(marked)
    measured = sum(1 for row in marked if row["measured"])
    killed = sum(1 for row in marked if row["attributed"])
    state_backed_killed = sum(1 for row in marked
                              if row["attributed"] and row["state_backed"])
    missing = [name for name in CATEGORIES if name not in seen]
    conditions = {
        "sampled_at_least_ten": total >= 10,
        "measured": measured == total,
        "categories": len(missing) == 0,
        "rate": killed * BAR_A >= BAR_B * total,
        "state_backed": state_backed_killed >= 2,
    }
    gate = all(conditions.values())
    return {
        "total": total,
        "measured": measured,
        "killed": killed,
        "state_backed_killed": state_backed_killed,
        "missing": missing,
        "conditions": conditions,
        "gate": gate,
        "score_10": round(10.0 * killed / total, 1) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# L5c.5: the evidence document and its verifier, the controlling check.
#
# Everything below is ADDITIVE. The inventory, sample selection, hash lock,
# manifest, probe envelope, attribution and score gate above keep their exact
# names, defaults, outputs and statuses, so every existing caller is
# untouched.
#
# The estate's own failure class is answered by name here: a fix whose check
# cannot fail is not a fix. render_region(ledger) is a deterministic function
# of the ledger alone, verify_evidence compares the document region against
# that function byte for byte, and every other requirement is checked
# separately, so one drifted byte is never reported as a clean run.
# ---------------------------------------------------------------------------

#: The two markers L5C5-REQ-1 requires exactly once each.
L5C_BEGIN_MARKER = "<!-- L5C-GENERATED-BEGIN -->"
L5C_END_MARKER = "<!-- L5C-GENERATED-END -->"

#: L5C5-REQ-4: an evidence_quote is a substring of its recorded tail and at
#: most this many characters long.
EVIDENCE_QUOTE_MAX = 300

#: L5C5-REQ-5: the meta battery holds exactly this many rows, every one
#: KILLED and attributed.
META_BATTERY_SIZE = 9

#: L5C5-REQ-6: the five booleans score() already returns, all five present
#: and true.
GATE_CONDITIONS = ("sampled_at_least_ten", "measured", "categories",
                   "rate", "state_backed")


def render_region(ledger: Dict[str, Any]) -> str:
    """The exact bytes that must sit between the two markers.

    A deterministic function of the ledger alone: two ledgers that differ
    only in dict insertion order render the same string, and any real
    change to the ledger changes the region. A ledger that is not an
    object, or that json cannot serialize, raises ValueError rather than
    emitting half a region.
    """
    if not isinstance(ledger, dict):
        raise ValueError("ledger must be an object, found %s"
                         % type(ledger).__name__)
    try:
        canonical = json.dumps(ledger, sort_keys=True, ensure_ascii=False,
                               indent=1)
    except (TypeError, ValueError) as exc:
        raise ValueError("ledger cannot be rendered: %s" % exc)
    return "\nL5C-REGION\n```json\n" + canonical + "\n```\n"


def render_doc(doc_path: str, ledger: Dict[str, Any]) -> int:
    """Write render_region(ledger) between the two markers in doc_path.

    Returns the written region as a byte count. Both markers must occur
    exactly once in the file; a missing marker or a duplicate is refused
    before anything is written, because a document with two begin markers
    has no one region a verifier could compare against. Human text outside
    the markers is left byte for byte untouched.
    """
    _require_text(doc_path, "doc_path")
    if not isinstance(ledger, dict):
        raise ValueError("ledger must be an object, found %s"
                         % type(ledger).__name__)
    try:
        with open(doc_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("cannot read %r: %s" % (doc_path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("%r is not utf-8: %s" % (doc_path, exc))
    begin_hits = text.count(L5C_BEGIN_MARKER)
    end_hits = text.count(L5C_END_MARKER)
    if begin_hits != 1 or end_hits != 1:
        raise ValueError("marker count is not exactly one each: begin=%d end=%d"
                         % (begin_hits, end_hits))
    begin = text.index(L5C_BEGIN_MARKER) + len(L5C_BEGIN_MARKER)
    end = text.index(L5C_END_MARKER)
    if end < begin:
        raise ValueError("the end marker precedes the begin marker")
    region = render_region(ledger)
    payload = (text[:begin] + region + text[end:]).encode("utf-8")
    with open(doc_path, "wb") as handle:
        handle.write(payload)
    return len(region.encode("utf-8"))


def _evidence_lock_problems(ledger, root, problems):
    """L5C5-REQ-2: every locked hash still matches the tree now."""
    lock = ledger.get("lock")
    if not isinstance(lock, dict):
        problems.append("HASH: the ledger carries no lock object")
        return
    for rel in sorted(lock.keys()):
        expected = lock[rel]
        if not isinstance(rel, str) or not rel:
            problems.append("HASH: a lock key must be a non-empty path, "
                            "found %r" % (rel,))
            continue
        if not isinstance(expected, str) or not expected:
            problems.append("HASH: lock[%r] must be a hex digest, found %r"
                            % (rel, expected))
            continue
        try:
            full = _under_root(root, rel)
        except ValueError as exc:
            problems.append("HASH: %s" % exc)
            continue
        try:
            actual = sha256_file(full)
        except ValueError as exc:
            problems.append("HASH: NO-DATA: cannot hash %s: %s" % (rel, exc))
            continue
        if actual != expected:
            problems.append("HASH: %s is %s now, lock says %s"
                            % (rel, actual, expected))


def _evidence_anchor_problems(ledger, root, problems):
    """L5C5-REQ-3: every recorded old anchor resolves exactly once."""
    patches = ledger.get("patches")
    if not isinstance(patches, list):
        problems.append("ANCHOR: the ledger carries no patches list")
        return
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            problems.append("ANCHOR: patches[%d] must be an object, found %s"
                            % (index, type(patch).__name__))
            continue
        rel = patch.get("src_path")
        old = patch.get("old")
        new = patch.get("new")
        if not isinstance(rel, str) or not rel:
            problems.append("ANCHOR: patches[%d].src_path must be a non-empty "
                            "string, found %r" % (index, rel))
            continue
        if not isinstance(old, str) or not old:
            problems.append("ANCHOR: patches[%d].old must be a non-empty "
                            "string, found %r" % (index, old))
            continue
        if not isinstance(new, str):
            problems.append("ANCHOR: patches[%d].new must be a string, "
                            "found %s" % (index, type(new).__name__))
            continue
        try:
            full = _under_root(root, rel)
        except ValueError as exc:
            problems.append("ANCHOR: %s" % exc)
            continue
        try:
            with open(full, "rb") as handle:
                src_raw = handle.read()
        except OSError as exc:
            problems.append("ANCHOR: NO-DATA: cannot read %s: %s" % (rel, exc))
            continue
        try:
            src_text = src_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append("ANCHOR: NO-DATA: %s is not utf-8: %s"
                            % (rel, exc))
            continue
        _, problem = _unique_anchor_patch(src_text, old, new)
        if problem:
            problems.append("ANCHOR: patches[%d] in %s: %s"
                            % (index, rel, problem))


def _evidence_region_problems(doc_path, ledger, problems):
    """L5C5-REQ-1 and L5C5-REQ-3: exact markers, byte equal region."""
    try:
        with open(doc_path, "rb") as handle:
            doc_raw = handle.read()
    except OSError as exc:
        problems.append("REGION: NO-DATA: cannot read %s: %s"
                        % (doc_path, exc))
        return
    try:
        doc_text = doc_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append("REGION: NO-DATA: %s is not utf-8: %s"
                        % (doc_path, exc))
        return
    begin_hits = doc_text.count(L5C_BEGIN_MARKER)
    end_hits = doc_text.count(L5C_END_MARKER)
    if begin_hits != 1 or end_hits != 1:
        problems.append("REGION: marker count is not exactly one each: "
                        "begin=%d end=%d" % (begin_hits, end_hits))
        return
    begin = doc_text.index(L5C_BEGIN_MARKER) + len(L5C_BEGIN_MARKER)
    end = doc_text.index(L5C_END_MARKER)
    if end < begin:
        problems.append("REGION: the end marker precedes the begin marker")
        return
    actual_region = doc_text[begin:end]
    try:
        expected_region = render_region(ledger)
    except ValueError as exc:
        problems.append("REGION: cannot render the ledger: %s" % exc)
        return
    if actual_region != expected_region:
        problems.append("REGION: the generated region does not equal "
                        "render_region(ledger) byte for byte")


def _evidence_quote_problems(ledger, problems):
    """L5C5-REQ-4: each quote is a substring of its tail, max 300 chars."""
    evidence = ledger.get("evidence")
    if not isinstance(evidence, list):
        problems.append("QUOTE: the ledger carries no evidence list")
        return
    for index, row in enumerate(evidence):
        if not isinstance(row, dict):
            problems.append("QUOTE: evidence[%d] must be an object, found %s"
                            % (index, type(row).__name__))
            continue
        quote = row.get("evidence_quote")
        tail = row.get("output_tail")
        if not isinstance(quote, str) or not quote:
            problems.append("QUOTE: evidence[%d].evidence_quote must be a "
                            "non-empty string, found %r" % (index, quote))
            continue
        if not isinstance(tail, str):
            problems.append("QUOTE: evidence[%d].output_tail must be a "
                            "string, found %s" % (index, type(tail).__name__))
            continue
        if len(quote) > EVIDENCE_QUOTE_MAX:
            problems.append("QUOTE: evidence[%d] quote is %d chars, over the "
                            "%d maximum"
                            % (index, len(quote), EVIDENCE_QUOTE_MAX))
        if quote not in tail:
            problems.append("QUOTE: evidence[%d] quote is not a substring of "
                            "its recorded tail" % index)


def _evidence_meta_problems(ledger, problems):
    """L5C5-REQ-5: nine meta rows, every one KILLED and attributed."""
    meta = ledger.get("meta")
    if not isinstance(meta, list):
        problems.append("META: the ledger carries no meta list")
        return
    if len(meta) != META_BATTERY_SIZE:
        problems.append("META: the meta battery holds %d rows, the "
                        "specification requires %d"
                        % (len(meta), META_BATTERY_SIZE))
        return
    for index, row in enumerate(meta):
        if not isinstance(row, dict):
            problems.append("META: meta[%d] must be an object, found %s"
                            % (index, type(row).__name__))
            continue
        if row.get("status") != "KILLED":
            problems.append("META: meta[%d] status is %r, not KILLED"
                            % (index, row.get("status")))
        if row.get("attributed") is not True:
            problems.append("META: meta[%d] attributed is %r, not True"
                            % (index, row.get("attributed")))


def _evidence_gate_problems(ledger, problems):
    """L5C5-REQ-6: the gate is true with all five booleans present."""
    gate = ledger.get("gate")
    if not isinstance(gate, dict):
        problems.append("GATE: the ledger carries no gate object")
        return
    if gate.get("gate") is not True:
        problems.append("GATE: gate is %r, not True" % (gate.get("gate"),))
    conditions = gate.get("conditions")
    if not isinstance(conditions, dict):
        problems.append("GATE: the gate carries no conditions object")
        return
    for name in GATE_CONDITIONS:
        if name not in conditions:
            problems.append("GATE: conditions is missing %s" % name)
            continue
        value = conditions[name]
        if not isinstance(value, bool):
            problems.append("GATE: conditions[%s] is %r, not a boolean"
                            % (name, value))
        elif value is not True:
            problems.append("GATE: conditions[%s] is False" % name)


def verify_evidence(doc_path: str, ledger_path: str,
                    root: str) -> List[str]:
    """Every reason this evidence document does not check out, as text.

    [] is returned only when every L5c.5 requirement passes: the lock
    hashes match the tree now, every recorded old anchor resolves exactly
    once through this module's own unique-anchor patch, the generated
    region equals render_region(ledger) byte for byte, every evidence_quote
    is a substring of its recorded tail and at most EVIDENCE_QUOTE_MAX
    characters, the meta battery is META_BATTERY_SIZE rows all KILLED and
    attributed, and the gate is true with all five GATE_CONDITIONS present
    and true.

    Anything else is one or more named problems, never a bare except and
    never an approval. A missing or unreadable ledger is NO-DATA, and the
    caller blocks on it. doc_path, ledger_path and root must be non-empty
    strings or ValueError is raised, so a None, a NaN or a bytes never
    reaches a downstream comparison.
    """
    _require_text(doc_path, "doc_path")
    _require_text(ledger_path, "ledger_path")
    _require_text(root, "root")
    try:
        with open(ledger_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return ["NO-DATA: cannot read the ledger %s: %s" % (ledger_path, exc)]
    try:
        ledger_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ["NO-DATA: the ledger %s is not utf-8: %s"
                % (ledger_path, exc)]
    try:
        ledger = json.loads(ledger_text)
    except ValueError as exc:
        return ["NO-DATA: the ledger %s is not JSON: %s" % (ledger_path, exc)]
    if not isinstance(ledger, dict):
        return ["NO-DATA: the ledger must be an object, found %s"
                % type(ledger).__name__]
    problems: List[str] = []
    _evidence_lock_problems(ledger, root, problems)
    _evidence_anchor_problems(ledger, root, problems)
    _evidence_region_problems(doc_path, ledger, problems)
    _evidence_quote_problems(ledger, problems)
    _evidence_meta_problems(ledger, problems)
    _evidence_gate_problems(ledger, problems)
    return list(problems)


