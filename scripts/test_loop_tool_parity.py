#!/usr/bin/env python3
"""Every tool the loop EXECUTES must also be a tool a reviewer can SEE.

The loop runs the copies in ~/.claude/bin. Review, the push gates and the repository history only
ever see scripts/loop. Nothing kept the two in step, so the split had two failure directions and
both were real when this was written on 2026-09-21:

  DRIFT      the same tool, different content, in the two places. The loop then runs code that no
             reviewer read and no gate scanned, while the repository shows something else. Measured
             the same evening: a diff of the loop tools returned 35 identical files at 19:43 and 34
             at 19:49, because one was edited in bin and the repository copy did not move.

  BIN ONLY   a tool that exists ONLY in bin, in no repository at all. Six were found, and the worst
             was commit_scan.py: the gate that scans every staged commit for secrets, long dashes,
             attribution trailers and private terms. A security control living on exactly one
             laptop, reviewed by nobody, backed up by nothing, and lost with the disk.

A control that depends on somebody remembering to copy a file is not a control. This is the check
that makes it one.

Run: python3 scripts/test_loop_tool_parity.py
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                     # THIS directory, not whatever sys.path offers
try:                                         # a trimmed export tree may not carry it: say so,
    import install_loop_tools as INSTALL     # noqa: E402  never quietly skip the two channels
except ImportError:
    INSTALL = None
REPO_DIRS = [os.path.join(HERE, "loop"), HERE]
BIN = os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin")))
# a repository only tool is SAFE: it is versioned, and it simply is not installed for the loop.
# The dangerous direction is the other one, so only that is a failure.
# Two ways versioned code names a tool, and the SECOND is the common one. The first version of
# this gate matched only a literal .claude/bin/<name> path and therefore exited 0 on the very
# defect it was written for: land_batch.py reaches commit_scan.py as os.path.join(BIN, "..."),
# which that pattern never saw. Caught by mutation, not by reading. So match any quoted script
# NAME as well, and let the existence test in bin decide whether it is a real reference.
REF_PATH = re.compile(r"[~/.]*\.claude/bin/([A-Za-z0-9_.-]+\.(?:py|sh))")
REF_NAME = re.compile(r"[\"']([A-Za-z0-9_.-]+\.(?:py|sh))[\"']")


def repo_copy(name):
    for d in REPO_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def referenced_by_loop(loop_dir=None):
    """Tool names a scripts/loop tool names by path or quoted string: what the loop actually RUNS."""
    loop_dir = loop_dir or REPO_DIRS[0]; used = set()
    for name in sorted(os.listdir(loop_dir)) if os.path.isdir(loop_dir) else []:
        body = read(os.path.join(loop_dir, name)) if name.endswith((".py", ".sh")) else None
        if body is not None:
            text = body.decode("utf-8", "replace"); used |= set(REF_PATH.findall(text)) | set(REF_NAME.findall(text))
    return used


def wrong_copy_installed(bin_dir=BIN, loop_dir=None, other_dir=None, referenced=None):
    """Installed tools whose bytes equal a same named file OUTSIDE scripts/loop while scripts/loop has, or had, its own.

    THE 2026-09-21 20:09 DEFECT, named so it cannot come back. A worker build landed a 116 line library as
    scripts/land_build.py (unit M2.1). The installer resolved the name by directory order, scripts/loop's copy was
    deleted the same evening as a duplicate, and scripts/land_build.py won: the executed landing tool had no main,
    exited 0 with no output, and every landing for a day and a half dropped as "fuzz crashes 99: no output". Two
    such pairs exist today (burn_guard.py, run_window.py) with the loop copy installed, which is fine; the failure is
    the OTHER copy being what the loop runs. Returns [(name, other path)]."""
    loop_dir = loop_dir or REPO_DIRS[0]; other_dir = other_dir or REPO_DIRS[1]
    referenced = referenced_by_loop(loop_dir) if referenced is None else referenced   # scoped to what the loop RUNS: a
    out = []                                                                          # tool nobody in the loop names is not its business
    for name in sorted(os.listdir(other_dir)) if os.path.isdir(other_dir) else []:
        if not name.endswith((".py", ".sh")) or name not in referenced:
            continue
        b, o = os.path.join(bin_dir, name), os.path.join(other_dir, name)
        if not os.path.isfile(b):
            continue
        bb, ob = read(b), read(o)
        if bb is not None and bb == ob:
            l = read(os.path.join(loop_dir, name))
            if l is None or l != bb:
                out.append((name, o))
    return out


STAMP = ".deploy-stamp.json"


def stamped_revision(bin_dir=BIN):
    """The revision the executed directory declares it was deployed from, or None.

    scripts/loop/deploy_stamped.py writes BIN/.deploy-stamp.json with the `revision` it copied the tools
    from. An unstamped directory, an unreadable stamp, or a revision that is not a full sha reads None,
    and the caller compares against this tree's copy exactly as before the stamp existed."""
    try:
        with open(os.path.join(bin_dir, STAMP), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    revision = doc.get("revision") if isinstance(doc, dict) else None
    if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision):
        return revision
    return None


def versioned_at(revision, path):
    """The bytes of `path` (a file inside this repository) as committed at `revision`, or None when git
    cannot show it (the revision is not in this repository, or the file did not exist there)."""
    top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0 or not top.stdout.strip():
        return None
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top.stdout.strip()))
    if rel.startswith(".."):
        return None
    shown = subprocess.run(["git", "-C", HERE, "show", "%s:%s" % (revision, rel)], capture_output=True)
    return shown.stdout if shown.returncode == 0 else None


def drifted():
    """Tools present in BOTH places whose bytes differ. Returns (drifted, at_revision): the second list
    names tools that differ from THIS tree's copy but equal the versioned copy at the revision the
    executed directory was deployed from (stamped_revision), which is not a drift.

    WHY THE STAMP: this check joined the merge gate on 2026-09-26, and on 2026-09-30 it failed pristine
    hub main three times (loop_done.py, loop_procs.py, stop_loop.sh) because the deployed loop was cut from
    the run line, a branch ahead of main. Nothing was hand edited, nothing ran code the repository does not
    hold: the tree under merge and the deployed tree were simply different commits, and a merge gate must
    answer for the tree under merge, not for which commit the operator last deployed (unit R4: no test
    reads real machine state). So the executed copy is compared against its OWN declared revision first;
    a copy matching neither this tree nor that revision is the drift this check exists for (a hand edit in
    bin, a deploy from a dirty tree, a half copied file), and it still fails. An unstamped directory, a
    stamp this repository cannot show, or a tool absent at that revision falls back to the comparison
    against this tree's copy, exactly as before.

    COMPARED AGAINST ONE CANONICAL COPY, not against any file sharing a basename. The first version
    compared the installed twin against EVERY repository file with that name, across scripts/loop
    and scripts. Those are different directories that may legitimately hold different tools with
    the same basename, and run_window.py is exactly that: scripts/run_window.py is the 61 line tool
    the battery's own test imports, and scripts/loop/run_window.py is the 80 line loop tool that is
    mirrored. Comparing the mirrored one's twin against the unmirrored namesake reported a drift
    that did not exist, while the real copies were byte identical.

    scripts/loop wins because it is the loop's own directory and the one that is mirrored. A
    namesake in scripts is reported as a SHADOW rather than a drift: worth knowing, because two
    tools with one name is how the wrong one gets edited, but not a failure of this check."""
    out, at_revision = [], []
    revision = stamped_revision()
    for name in sorted(set(n for d in REPO_DIRS if os.path.isdir(d) for n in os.listdir(d))):
        if not name.endswith((".py", ".sh")):
            continue
        b = os.path.join(BIN, name)
        if not os.path.isfile(b):
            continue
        canonical = repo_copy(name)          # scripts/loop first, by REPO_DIRS order
        if canonical is None:
            continue
        a_bytes, b_bytes = read(canonical), read(b)
        if a_bytes is None or b_bytes is None:
            out.append((name, "unreadable"))
        elif a_bytes != b_bytes:
            if revision is not None and versioned_at(revision, canonical) == b_bytes:
                at_revision.append((name, revision))
            else:
                out.append((name, "differs"))
    return out, at_revision


def shadows():
    """One basename, two repository directories, different content. Not a drift, but worth naming:
    two tools with one name is how the wrong one gets edited for an hour."""
    out = []
    loop_dir, scripts_dir = REPO_DIRS[0], REPO_DIRS[1]
    if not (os.path.isdir(loop_dir) and os.path.isdir(scripts_dir)):
        return out
    for name in sorted(os.listdir(loop_dir)):
        if not name.endswith((".py", ".sh")):
            continue
        other = os.path.join(scripts_dir, name)
        if os.path.isfile(other) and read(os.path.join(loop_dir, name)) != read(other):
            out.append(name)
    return out


def bin_only_but_used():
    """Tools referenced BY versioned code that live only in bin, so the loop runs what no
    reviewer sees. Referenced is the test, not merely present: bin holds plenty that this
    repository has no opinion about."""
    used = set()
    for d in REPO_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith((".py", ".sh")):
                continue
            body = read(os.path.join(d, name))
            if body is None:
                continue
            text = body.decode("utf-8", "replace")
            used |= set(REF_PATH.findall(text)) | set(REF_NAME.findall(text))
    missing = []
    for name in sorted(used):
        if os.path.isfile(os.path.join(BIN, name)) and repo_copy(name) is None:
            missing.append(name)
    return missing




# ---------------------------------------------------------------------------
# THE AMBIGUITY, which is the root of three of the four incidents of 2026-09-21
# ---------------------------------------------------------------------------
# Bytes are only one of the ways the two copies diverge, and the quietest divergence is not in the
# files at all, it is in the READER. A test that says `import model_router` binds whichever copy
# sys.path, the cwd or PYTHONPATH happened to offer. It can pass against the repository copy while
# the loop executes the installed one, which is worse than having no test: it turns ambiguity into
# confidence. That is exactly how a mutation applied to one copy "survived" and a worker's correct
# claim was reported as wrong, twice, in both directions, and how an external suite read the real
# module while the verifier mutated a copy.
#
# So this section answers, for every reader of a mirrored module name, ONE question: which file
# does it bind? It prints the answer, because visibility is the point, and it FAILS on the two
# shapes that mean nobody can know:
#
#   UNDETERMINED  the reader names a mirrored module and no directory it puts on sys.path holds
#                 that module, so the binding is whatever the environment decides at run time.
#   SPLIT         two readers of ONE mirrored name bind repository files with DIFFERENT bytes.
#                 Mutate one and the other keeps saying green. Where the split is entirely
#                 explained by a shadow this gate already reports by name, it is that finding,
#                 not a second one, and it is not counted twice.
IMPORT = re.compile(r"^[ \t]*(?:import[ \t]+([A-Za-z_]\w*)|from[ \t]+([A-Za-z_]\w*)[ \t]+import)",
                    re.M)
INSERT = re.compile(r"sys\.path\.insert\s*\(\s*\d+\s*,\s*(.+)\)")
SLICE = re.compile(r"sys\.path\[\s*:\s*0\s*\]\s*=\s*\[")


def _slice_entries(text, open_at):
    """The top level comma separated expressions of the list whose '[' is at text[open_at - 1], or None when the
    list never closes. Brackets and parentheses are matched, so f(C, [1, 2]) stays one entry."""
    depth, cur, out = 0, "", []
    for ch in text[open_at:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                if cur.strip():
                    out.append(cur.strip())
                return out
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip()); cur = ""
        else:
            cur += ch
    return None


def search_order(text):
    """Every directory expression `text` puts in front of sys.path, in the order the interpreter then searches
    them: the latest prepend first, and a slice prepend (sys.path[:0] = [a, b]) left to right. Both forms count:
    lanes wrote the slice form four times on 2026-09-26 and this gate, reading only insert, refused each deploy."""
    ops = [(m.start(), [m.group(1).strip()]) for m in INSERT.finditer(text)]
    for m in SLICE.finditer(text):
        entries = _slice_entries(text, m.end())
        if entries:
            ops.append((m.start(), entries))
    order = []
    for _, exprs in sorted(ops, key=lambda op: op[0], reverse=True):
        order.extend(exprs)
    return order


def mirrored_modules():
    loop = os.path.join(HERE, "loop")
    if not os.path.isdir(loop):
        return set()
    return {n[:-3] for n in os.listdir(loop)
            if n.endswith(".py") and os.path.isfile(os.path.join(BIN, n))}


def imports_of(text):
    """[(module, offset of the import statement itself)] for every REAL top level name imported by the file, read by parsing, never run.
    2026-09-23: the regex also matched a usage example inside provenance.py's docstring ("import provenance as P") and
    failed parity the moment the installer made provenance a mirrored tool. A file that does not parse falls back to the
    line regex, so a syntax error never hides an import; the fallback is the old behaviour, never less."""
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [(m.group(1) or m.group(2), m.start()) for m in IMPORT.finditer(text)]
    starts = [0]
    for line in text.splitlines(True): starts.append(starts[-1] + len(line))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name.split(".")[0], starts[node.lineno - 1] + node.col_offset) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append((node.module.split(".")[0], starts[node.lineno - 1] + node.col_offset))
    return sorted(out, key=lambda x: x[1])


def declared_dir(expr, reader_dir, text="", _depth=0):
    """Which directory a sys.path.insert expression NAMES. Not evaluated: a gate that executes the
    tree it is judging is a gate that can be made to lie by the tree it is judging.
    A BARE NAME is read through its own assignment in the reader's text (2026-09-23: scripts/probe_brief.py
    inserts `_HERE`, which `\\bHERE\\b` cannot match inside, so a correctly pinned import read as ambiguous and
    refused every deploy). Still a read, never an evaluation; a name with no readable assignment stays None."""
    if _depth < 3 and text and re.fullmatch(r"[A-Za-z_]\w*", expr):
        rhs = re.findall(r"^[ \t]*%s[ \t]*=[ \t]*([^;\n]+)" % re.escape(expr), text, flags=re.M)
        if rhs:
            return declared_dir(rhs[-1].strip(), reader_dir, text, _depth + 1)
    if "claude/bin" in expr or re.search(r"\bBIN\b", expr):
        return BIN
    if "loop" in expr.lower():
        return os.path.join(HERE, "loop")
    if "__file__" in expr or re.search(r"\bHERE\b", expr):
        return reader_dir
    return None


def bindings():
    """(reader, module, bound file or None) for every import of a mirrored module in this tree."""
    out = []
    mods = mirrored_modules()
    if not mods:
        return None
    for d in REPO_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            reader = os.path.join(d, name)
            body = read(reader)
            if body is None:
                continue
            text = body.decode("utf-8", "replace")
            for mod, start in imports_of(text):
                if mod not in mods:
                    continue
                bound = None
                # later inserts at position 0 are searched FIRST, so read them newest first
                for expr in search_order(text[:start]):
                    cand = declared_dir(expr.strip(), d, text[:start])
                    if cand and os.path.isfile(os.path.join(cand, mod + ".py")):
                        bound = os.path.join(cand, mod + ".py")
                        break
                out.append((reader, mod, bound))
    return out


def ambiguous(binds, known_shadows):
    """The two failing shapes, plus the table that makes every binding visible."""
    undetermined = [(r, m) for r, m, b in binds if b is None]
    by_module = {}
    for r, m, b in binds:
        if b is None or os.path.dirname(b) == BIN:
            continue                          # the installed copy is the drift check's business
        by_module.setdefault(m, set()).add(b)
    split = []
    for mod, paths in sorted(by_module.items()):
        if len(paths) < 2:
            continue
        if len({read(p) for p in paths}) == 1:
            continue                          # several paths, one content: no subject to split
        if mod + ".py" in known_shadows:
            continue                          # already named above as a shadow; not a second find
        split.append((mod, sorted(paths)))
    return undetermined, split


def main():
    if not os.path.isdir(BIN):
        # NOT APPLICABLE is not the same as NO-DATA, and this is the one place the difference is
        # real. This gate asks "does the INSTALLED loop match the repository". Where no loop is
        # installed under this HOME, as in the hermetic export tree the push gate builds, there is
        # no executed copy to drift from and no stale loop can run, so the claim is not unknown,
        # it is vacuously safe. The failure this gate prevents needs an installed loop to exist.
        # Everywhere a loop IS installed, the full check below runs and NO-DATA stays forbidden.
        print("NOT APPLICABLE: no loop is installed under this HOME (%s), so there is no executed "
              "copy that could drift from the repository" % BIN)
        return 0
    (d, at_revision), m = drifted(), bin_only_but_used()
    sh = shadows()
    for name, revision in at_revision:
        print("note revision  %-28s equals the versioned copy at the deployed revision %s, not this "
              "tree's copy" % (name, revision[:12]))
    for name in sh:
        print("note shadow    %-28s exists in scripts/loop AND scripts with different content; "
              "the loop uses scripts/loop" % name)
    # the channels a byte comparison cannot see, verified present in this estate on 2026-09-21:
    # 27 cached modules in the installed directory, and one file whose mode differs.
    install = []
    if INSTALL is None:
        print("NO-DATA install  install_loop_tools.py is not in this tree, so the stale bytecode "
              "and mode channels were not checked")
    for name in sorted(set(n for dd in REPO_DIRS if os.path.isdir(dd) for n in os.listdir(dd))):
        if not name.endswith((".py", ".sh")) or not os.path.isfile(os.path.join(BIN, name)):
            continue
        if repo_copy(name) is None or INSTALL is None:
            continue
        why = INSTALL.stale_pyc(BIN, name) or INSTALL.mode_mismatch(REPO_DIRS[0], BIN, name)
        if why:
            install.append((name, why))
    binds = bindings()
    wrong = wrong_copy_installed()
    for name, other in wrong:
        print("FAIL wrong-copy %-25s the loop runs %s, a file outside scripts/loop that won by name" % (name, other))
    undetermined, split = ([], []) if binds is None else ambiguous(binds, set(sh))
    if binds:
        for reader, mod, bound in binds:
            print("bind %-40s %-16s -> %s"
                  % (os.path.relpath(reader, HERE), mod,
                     os.path.relpath(bound, HERE) if bound else "UNDETERMINED"))
    for name, why in install:
        print("FAIL install  %-28s %s" % (name, why))
    for reader, mod in undetermined:
        print("FAIL ambiguous %-27s imports %s without saying which copy it means"
              % (os.path.relpath(reader, HERE), mod))
    for mod, paths in split:
        print("FAIL split    %-28s is read as %s by different readers; mutate one and the other "
              "still says green" % (mod, " and ".join(os.path.relpath(p, HERE) for p in paths)))
    for name, why in d:
        print("FAIL drift    %-28s the executed copy %s from the versioned one" % (name, why))
    for name in m:
        print("FAIL bin only %-28s referenced by versioned code but exists in no repository copy" % name)
    if d or m or install or undetermined or split or wrong:
        print("\n%d wrong copy installed, " % len(wrong) + "%d drifted, %d unversioned, %d badly installed, %d ambiguous import(s), %d split "
              "subject(s). The loop is running, or being tested against, code the repository does "
              "not hold." % (len(d), len(m), len(install), len(undetermined), len(split)))
        return 1
    print("PASS: every executed loop tool matches its versioned copy, none referenced by "
          "versioned code is missing from the repository, the installation carries no stale "
          "bytecode or wrong mode, and every reader of a mirrored module names the copy it means")
    return 0


if __name__ == "__main__":
    sys.exit(main())
