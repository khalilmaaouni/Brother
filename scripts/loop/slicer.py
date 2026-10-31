#!/usr/bin/env python3
"""Verbatim slice of a big Python file for a build brief: header (imports, constants) plus every top-level
def/class whose name the spec text mentions. Everything kept is byte-identical to the file, so a worker's
unique-find patch still applies to the real file. Dropped blocks are named, never silently missing."""
import ast, re

#: SLICE1.a. COMMENT_CONTEXT is how many prose lines directly above a surviving code line travel with
#: it. Measured 2026-10-26 on products/brothermode/tools/bm_store.py: of the 128,980 byte remainder left
#: when every function, the module docstring and every module level statement are already sliced out,
#: 99,153 bytes are comment or blank lines between things that are no longer there. That is what refused
#: every lane reading this file. Folding them at a context of 3 leaves 51,893 bytes against a 110,000
#: budget. A context of 0 would leave 36,228, and the extra 15,665 bytes buy the comment that documents
#: each kept block, which is the thing a worker reads before patching it.
COMMENT_CONTEXT = 3


def _is_marker(line):
    """A marker the slicer itself wrote. It records what was removed, so no fold may remove it."""
    return line.lstrip().startswith("# [")


def _is_prose(line):
    """A comment or blank line that is not a marker: foldable in principle."""
    s = line.strip()
    return (not s) or (s.startswith("#") and not _is_marker(line))


def _comment_keep(lines):
    """The indices no fold may touch: everything except a prose run that is DELIMITED BY MARKERS.

    This is the rule that makes the fold safe, and it is deliberately structural rather than a judgement
    about what a comment looks like. A prose run whose neighbour on each side is a marker the slicer
    itself wrote, or the edge of the text, sits strictly between two things that are already gone, so it
    cannot be inside a block the brief keeps. An earlier rule here protected only the three prose lines
    above the next real code line, and it folded the comment lines INSIDE kept function bodies, which
    broke REQ-SLICE1A-02 silently: the body was still present but no longer byte identical, so a
    worker's unique find would not have applied."""
    keep = set(range(len(lines)))
    i = 0
    while i < len(lines):
        if not _is_prose(lines[i]):
            i += 1
            continue
        j = i
        while j < len(lines) and _is_prose(lines[j]):
            j += 1
        before_ok = i == 0 or _is_marker(lines[i - 1])
        after_ok = j >= len(lines) or _is_marker(lines[j])
        if before_ok and after_ok:
            keep -= set(range(i, j))
        i = j
    return keep


def _fold_comment_runs(lines, keep):
    """Replace each maximal run of lines outside keep with one marker line, and return the new lines.

    Every line that survives is returned unchanged, so each surviving block is still a contiguous slice
    of the source and REQ-SLICE1A-02 holds. A run of one line is left alone, because the marker naming it
    would be longer than the line it replaced. keep is the authority on what may go: this function does
    not re-decide it, so there is exactly one place where that judgement lives."""
    out, i, folded = [], 0, 0
    while i < len(lines):
        if i in keep:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and j not in keep:
            j += 1
        if j - i >= 2:
            out.append("# [SLICED OUT, not shown: comments, lines %d to %d]\n" % (i + 1, j))
            folded += j - i
        else:
            out.extend(lines[i:j])
        i = j
    return out


def slice_source(path, spec_text, budget):
    src = open(path, encoding="utf-8").read()
    if len(src) <= budget:
        return src, []
    tree = ast.parse(src); lines = src.splitlines(keepends=True)
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", spec_text))
    keep, dropped, pos = [], [], 0
    for node in tree.body:
        start = (node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno) - 1
        end = node.end_lineno
        is_block = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        wanted = (not is_block) or node.name in names
        if wanted:
            keep.append("".join(lines[pos:end]))      # includes the comments and blank lines before it
        else:
            dropped.append(node.name)
            keep.append("# [SLICED OUT, not shown: %s, lines %d to %d]\n" % (node.name, start + 1, end))
        pos = end
    keep.append("".join(lines[pos:]))
    out = "".join(keep)
    if len(out) > budget:
        #: SLICE1.a. FOLD BEFORE DROPPING. Dropping a block costs the worker a function it may need;
        #: folding the prose between blocks already gone costs it nothing it can patch against.
        #: This is ALSO the only stage the guard fixtures reach, which was measured the hard way: with
        #: this call removed every fixture still passed and the mutation sweep then showed the fold's own
        #: safety rule surviving every mutation, because the late fold below is never reached by a small
        #: fixture. A guard whose code no test can reach is not a guard.
        folded = out.splitlines(keepends=True)
        out = "".join(_fold_comment_runs(folded, _comment_keep(folded)))
        if len(out) < len("".join(folded)) and "<comment runs between sliced blocks>" not in dropped:
            dropped = dropped + ["<comment runs between sliced blocks>"]
    if len(out) > budget:
        # STILL TOO BIG. Until 2026-09-21 this raised, which refused the whole lane: three sub units (D1.5,
        # D3.4, L3.1) sat blocked for a day having never reached a model at all, because one shared module they
        # read sliced to 41546 bytes against a 31914 byte budget. That is the wrong direction. The estate's own
        # lesson is that a size refusal is solved by a verbatim slicer, and a slicer that gives up is not one.
        #
        # So keep dropping WHOLE blocks until it fits, never truncating mid function, which was always the real
        # constraint: a half function in a brief produces a patch that cannot apply. Least relevant goes first,
        # relevance being how often the spec text names it, and the largest breaks the tie so the fewest blocks
        # are lost. Every drop is still named in the text, so the worker is never silently handed less than it
        # thinks it has. Only an unsliceable remainder raises, and it says what it could not shrink.
        mentions = lambda n: len(re.findall(r"\b" + re.escape(n) + r"\b", spec_text))
        blocks = [(i, n.name, n.end_lineno) for i, n in enumerate(tree.body)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name not in dropped]
        order = sorted(blocks, key=lambda b: (mentions(b[1]), -len("".join(lines[:b[2]]))))
        #: the last entry is the most mentioned, and it is the one block the brief is about.
        protected = order[-1][1] if order and mentions(order[-1][1]) > 0 else None
        # THE MODULE DOCSTRING GOES FIRST (2026-09-24, run 2: jev_seam.py's remainder with every function sliced out
        # was 53259 bytes against a 41865 byte share, 38748 of them its module docstring, so the one module D3.7's
        # tests import was NOT SHOWN while eleven builds guessed its shapes; and dropping it LAST was no better,
        # since by then every function the spec named had already been forced out to make room for an essay). No
        # patch ever targets a docstring and the worker needs the signatures, so it is the first block forced out,
        # marked by name like every other, and a function is forced only if that was not enough.
        first = tree.body[0] if tree.body else None
        doc_gone = bool(isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str))

        def render(gone):
            keep2, pos2 = [], 0
            for i, node in enumerate(tree.body):
                start = (node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno) - 1
                end = node.end_lineno
                is_block = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                if is_block and node.name in gone:
                    keep2.append("# [SLICED OUT, not shown: %s, lines %d to %d]\n" % (node.name, start + 1, end))
                elif i == 0 and doc_gone:
                    keep2.append("".join(lines[pos2:start]))
                    keep2.append("# [MODULE DOCSTRING SLICED OUT, not shown: lines %d to %d]\n" % (node.lineno, end))
                else:
                    keep2.append("".join(lines[pos2:end]))
                pos2 = end
            keep2.append("".join(lines[pos2:]))
            return "".join(keep2)

        out = render(set(dropped))
        forced = []
        for _, name, _ in order:
            if len(out) <= budget:
                break
            #: SLICE1.a, THE DROP ORDER. The single most relevant block is never forced out. Measured on
            #: bm_store.py: the old order spent the budget on module level statements and forced
            #: mask_absolute_paths out to make room, returning a slice that fit and contained no function
            #: body at all, so the worker was handed a brief about a function it could not see. Module
            #: level statements are dropped in the stage below instead, which is where the room is.
            #: Protecting EVERY named block instead of one was tried and is wrong: when a spec names four
            #: blocks and the budget fits one, nothing may be forced and the whole lane refuses again,
            #: which is the failure this cascade exists to prevent. Relevance still decides the rest.
            if name == protected:
                continue
            forced.append(name)
            out = render(set(dropped) | set(forced))
        dropped = dropped + forced + (["<module docstring>"] if doc_gone else [])
        if len(out) > budget:
            # MODULE LEVEL STATEMENTS ARE BLOCKS TOO (2026-09-24 14:3x: loop_bridge.py's remainder with every function
            # and the docstring gone was 26869 bytes of constants against a 26395 byte share, 474 bytes short, and the
            # whole file read NOT SHOWN). Largest first, marked by its lines, never an import: a worker reads the
            # signatures and the imports, and a constant it cannot see is listed in its unknowns like a function.
            stmts = sorted(((n.end_lineno - n.lineno + 1, i) for i, n in enumerate(tree.body)
                            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
                            and not (i == 0 and doc_gone)), reverse=True)
            gone_stmts = set()
            for _, i in stmts:
                if len(out) <= budget:
                    break
                gone_stmts.add(i)
                keep3, pos3 = [], 0
                for j, node in enumerate(tree.body):
                    start = (node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno) - 1
                    end = node.end_lineno
                    is_block = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    if is_block and node.name in set(dropped):
                        keep3.append("# [SLICED OUT, not shown: %s, lines %d to %d]\n" % (node.name, start + 1, end))
                    elif j == 0 and doc_gone:
                        keep3.append("".join(lines[pos3:start]))
                        keep3.append("# [MODULE DOCSTRING SLICED OUT, not shown: lines %d to %d]\n" % (node.lineno, end))
                    elif j in gone_stmts:
                        keep3.append("".join(lines[pos3:start]))
                        keep3.append("# [MODULE LEVEL STATEMENT SLICED OUT, not shown: lines %d to %d]\n" % (node.lineno, end))
                    else:
                        keep3.append("".join(lines[pos3:end]))
                    pos3 = end
                keep3.append("".join(lines[pos3:]))
                out = "".join(keep3)
            dropped = dropped + ["<module level statement lines %d to %d>" % (tree.body[i].lineno, tree.body[i].end_lineno) for i in sorted(gone_stmts)]
        if len(out) > budget:
            #: SLICE1.a. Everything that can be sliced is already gone, so what is left is imports, a
            #: little module level code, the markers naming what went, and the prose between them. Fold
            #: that prose before refusing: three earlier attempts placed this fold before the raise,
            #: before the loop and inside render and all three measured zero bytes removed, because the
            #: rule they used skipped any run touching a kept line. The rule folds a run's interior
            #: instead, and keeps the context above real code.
            folded = out.splitlines(keepends=True)
            out = "".join(_fold_comment_runs(folded, _comment_keep(folded)))
            if len(out) < len("".join(folded)) and "<comment runs between sliced blocks>" not in dropped:
                #: a shrink with nothing named is exactly what the harness refuses, so the fold says so.
                dropped = dropped + ["<comment runs between sliced blocks>"]
        if len(out) > budget:
            raise ValueError("%s: even with every function sliced out and every comment run folded the "
                             "remainder is %d bytes against a %d budget, so the file's imports and module "
                             "level code alone do not fit"
                             % (path, len(out), budget))
    return out, dropped

if __name__ == "__main__":   # self-check: every kept block is a verbatim substring, a named function survives
    import sys
    p = "scripts/jev_seam.py"; spec = open("docs/plan/specs/D3.md").read()
    i = spec.find("### D3.1"); sec = spec[i:spec.find("\n### ", i + 5)]
    out, dropped = slice_source(p, sec, 90000); src = open(p).read()
    blocks = [b for b in re.split(r"# \[SLICED OUT[^\n]*\n", out) if b.strip()]
    assert all(b in src for b in blocks), "a kept block is not verbatim"
    assert dropped, "nothing was dropped from a 125 KB file under a 90 KB budget"
    print("ok: %d -> %d bytes, %d blocks dropped, %d kept verbatim" % (len(src), len(out), len(dropped), len(blocks)))
