"""L5e.6 mutation red runner.

One named mutation at a time is applied to a temporary copy of this
repository's scripts directory, and the sub unit check that the mutation is
meant to break is run inside that copy. ``run_mutation`` returns that
check's exit code, so a zero return means the mutation was NOT caught and is
itself a block.

Every failure direction blocks rather than passing:

  * an unknown mutation name raises ValueError;
  * a mutation whose find string is absent, or present more than once, is an
    already applied or ambiguous mutation and raises ValueError;
  * a second mutation running at the same time raises ValueError;
  * a temporary copy that cannot be removed raises ValueError, because a
    mutation that left the tree dirty was never a clean proof;
  * a check module that cannot be imported, that raises while it is
    imported, or that runs zero tests, is RED (nonzero), never green.

The check is loaded from the temporary copy with unittest's own loader once
the copy's scripts directory sits at the front of sys.path, so there is no
subprocess, no exec and no module name built at runtime.

Standard library only, Python 3.9 syntax.
"""

import os
import sys
import tempfile
import threading
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MUTATIONS = {
    "M-L5E1-ALWAYS-DIFF": {
        "path": os.path.join("scripts", "system_doc.py"),
        "find": '    return "".join(diff_lines)\n',
        "replace": '    return "mutation forced a diff"\n',
        "check_module": "test_l5e_1_system_doc_diff",
    },
    "M-L5E2-EMPTY-CLAIMS": {
        "path": os.path.join("scripts", "test_l5e_2_parity_matrix_claims.py"),
        "find": "    return claims\n",
        "replace": "    return []\n",
        "check_module": "test_l5e_2_parity_matrix_claims",
    },
    "M-L5E3-ALWAYS-OK": {
        "path": os.path.join("scripts", "test_l5e_3_readme_install.py"),
        "find": '            "status": "REFUSED",\n',
        "replace": '            "status": "OK",\n',
        "check_module": "test_l5e_3_readme_install",
    },
    "M-L5E4-MISSING-SECTION": {
        "path": os.path.join("scripts", "system_doc.py"),
        "find": "        order.append(key)\n",
        "replace": '        order.append(key if key != "system.md accuracy" else "parity-matrix accuracy")\n',
        "check_module": "test_l5e_4_audit_doc_assembly",
    },
    "M-L5E5-ALWAYS-TEN": {
        "path": os.path.join("scripts", "l5e_5_score_gate.py"),
        "find": "    return average\n",
        "replace": "    return 10.0\n",
        "check_module": "test_l5e_5_score_gate",
    },
}

MUTATION_NAMES = tuple(sorted(MUTATIONS))

_MANAGED_MODULES = (
    "system_doc",
    "l5e_5_score_gate",
    "tmp_sandbox",
    "test_l5e_1_system_doc_diff",
    "test_l5e_2_parity_matrix_claims",
    "test_l5e_3_readme_install",
    "test_l5e_4_audit_doc_assembly",
    "test_l5e_5_score_gate",
)

_RUN_LOCK = threading.Lock()


def _read_text(path):
    """Return the utf-8 text of path, or raise ValueError.

    A missing, unreadable or non utf-8 file is refused rather than guessed
    at, because a mutation applied to a file whose bytes are unknown was
    never a clean proof.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not utf-8: %s" % (path,))


def _copy_tree(source, destination):
    """Copy every file under source into destination, skipping caches."""
    if not isinstance(source, str) or not source:
        raise ValueError("copy source must be a non empty string")
    if not isinstance(destination, str) or not destination:
        raise ValueError("copy destination must be a non empty string")
    if not os.path.isdir(source):
        raise ValueError("copy source is not a directory: %s" % (source,))
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [name for name in dirnames
                       if name not in (".git", "__pycache__")]
        relative = os.path.relpath(dirpath, source)
        target = (destination if relative == "."
                  else os.path.join(destination, relative))
        if not os.path.isdir(target):
            try:
                os.makedirs(target)
            except OSError as exc:
                raise ValueError("cannot create %s: %s" % (target, exc))
        for name in filenames:
            source_file = os.path.join(dirpath, name)
            target_file = os.path.join(target, name)
            try:
                with open(source_file, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise ValueError("cannot read %s: %s" % (source_file, exc))
            try:
                with open(target_file, "wb") as handle:
                    handle.write(data)
            except OSError as exc:
                raise ValueError("cannot write %s: %s" % (target_file, exc))


def _remove_tree(root):
    """Remove a tree this module built, naming anything it cannot remove.

    A removal that fails is reported on stderr and never pretended away; the
    caller decides whether the leftover blocks.
    """
    if not isinstance(root, str) or not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                os.remove(path)
            except OSError as exc:
                sys.stderr.write("l5e6: cannot remove %s: %s\n" % (path, exc))
        for name in dirnames:
            path = os.path.join(dirpath, name)
            try:
                os.rmdir(path)
            except OSError as exc:
                sys.stderr.write("l5e6: cannot remove %s: %s\n" % (path, exc))
    try:
        os.rmdir(root)
    except OSError as exc:
        sys.stderr.write("l5e6: cannot remove %s: %s\n" % (root, exc))


def _apply_mutation(root, mutation):
    """Replace exactly one occurrence of the find string in the target file.

    Zero occurrences is an already applied or corrupt mutation, more than one
    is an ambiguous mutation; both raise ValueError rather than editing the
    wrong line.
    """
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non empty string")
    if not isinstance(mutation, dict):
        raise ValueError("mutation must be a mapping")
    target = os.path.join(root, mutation["path"])
    if not os.path.isfile(target):
        raise ValueError("mutation target is missing: %s" % (target,))
    text = _read_text(target)
    found = text.count(mutation["find"])
    if found != 1:
        raise ValueError(
            "the find string of this mutation occurs %d times in %s"
            % (found, target))
    try:
        with open(target, "wb") as handle:
            handle.write(text.replace(mutation["find"],
                                      mutation["replace"]).encode("utf-8"))
    except OSError as exc:
        raise ValueError("mutation target is unwritable: %s: %s" % (target, exc))


def _run_check(check_module, temp_root):
    """Run one check module from a temporary copy and return its exit code.

    Zero means the check passed, which blocks a mutation that was meant to
    turn it red. Nonzero means the check went red. A check module that cannot
    be imported, that raises while it is imported, or that runs zero tests is
    red as well: a check that did not run is never a pass.
    """
    if not isinstance(check_module, str) or not check_module:
        raise ValueError("check_module must be a non empty string")
    if not isinstance(temp_root, str) or not temp_root:
        raise ValueError("temp_root must be a non empty string")
    scripts_dir = os.path.join(temp_root, "scripts")
    if not os.path.isdir(scripts_dir):
        raise ValueError("the temporary copy has no scripts directory")
    saved_path = list(sys.path)
    saved_modules = {}
    for name in _MANAGED_MODULES:
        if name in sys.modules:
            saved_modules[name] = sys.modules[name]
            del sys.modules[name]
    sys.path.insert(0, scripts_dir)
    try:
        # A check that cannot run at all is RED, never a pass.
        try:
            suite = unittest.TestLoader().loadTestsFromName(check_module)
            result = unittest.TestResult()
            suite.run(result)
        except Exception:
            return 1
        if result.testsRun == 0:
            return 1
        return 0 if result.wasSuccessful() else 1
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(check_module, None)
        for name in _MANAGED_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


def run_mutation(mutation_name, repo_root):
    """Apply a named mutation in a temp copy and return exit code of its check.

    An unknown mutation name, an already applied or ambiguous mutation, a
    concurrent mutation, a missing target, a temporary copy that cannot be
    removed, and every hostile or missing argument raise ValueError.
    """
    if not isinstance(mutation_name, str) or not mutation_name:
        raise ValueError("mutation_name must be a non empty string")
    if mutation_name not in MUTATIONS:
        raise ValueError("unknown mutation name: %r" % (mutation_name,))
    if not isinstance(repo_root, str) or not repo_root:
        raise ValueError("repo_root must be a non empty string")
    if not os.path.isdir(repo_root):
        raise ValueError("repo_root is not a directory: %r" % (repo_root,))
    mutation = MUTATIONS[mutation_name]
    target = os.path.join(repo_root, mutation["path"])
    if not os.path.isfile(target):
        raise ValueError("mutation target is missing: %s" % (target,))
    if _read_text(target).count(mutation["find"]) != 1:
        raise ValueError(
            "mutation %s is already applied, or its find string is not "
            "unique in %s" % (mutation_name, target))
    if not _RUN_LOCK.acquire(False):
        raise ValueError("another mutation is already running")
    try:
        temp_root = tempfile.mkdtemp(prefix="l5e6-mutation-")
        try:
            _copy_tree(os.path.join(repo_root, "scripts"),
                       os.path.join(temp_root, "scripts"))
            _apply_mutation(temp_root, mutation)
            return _run_check(mutation["check_module"], temp_root)
        finally:
            _remove_tree(temp_root)
            if os.path.isdir(temp_root):
                raise ValueError(
                    "mutation %s left its temporary copy behind: %s"
                    % (mutation_name, temp_root))
    finally:
        _RUN_LOCK.release()
