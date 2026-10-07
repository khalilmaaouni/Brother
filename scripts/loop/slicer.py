#!/usr/bin/env python3
"""Verbatim slice of a big Python file for a build brief: header (imports, constants) plus every top-level
def/class whose name the spec text mentions. Everything kept is byte-identical to the file, so a worker's
unique-find patch still applies to the real file. Dropped blocks are named, never silently missing."""
import ast, re

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
            raise ValueError("%s: even with every function sliced out the remainder is %d bytes against a %d "
                             "budget, so the file's imports and module level code alone do not fit"
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
