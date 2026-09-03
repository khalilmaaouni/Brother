#!/usr/bin/env python3
"""Replay docs/guides/05-a-worked-engagement.md against the live tools.

The guide opens with the claim that every command was run and every output
block is what it printed. That claim went stale silently once: a formatting
change landed in the tools, every quoted verdict block kept the old shape, and
nothing failed, because the byte-exactness was checked by hand once and then
asserted forever. This harness makes the claim mechanical: it extracts every
fenced block from the guide, writes the artifact blocks byte for byte into a
scratch engagement, runs every command block in order, captures what each one
printed, and diffs that against the output block the guide shows underneath.

Run:  python3 evals/replay_guide05.py            (report; exit 1 if any differ)
      python3 evals/replay_guide05.py --write    (patch stale blocks in place,
                                                  from the live output, so a
                                                  maintainer never hand-types
                                                  expected output)

Wired into evals/run_evals.py as a docs-class eval, so drift fails the gate.
Standard library only, no network; commands run in a throwaway directory.
"""
import difflib, io, os, re, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(REPO, "docs", "guides", "05-a-worked-engagement.md")
WRITE = "--write" in sys.argv

FILE_HEADS = {"# 01.": "01-purpose.md", "# 02.": "02-process.md", "# 03.": "03-adr.md",
              "# 04.": "04-technology-map.md", "# 05.": "05-data-model.md",
              "# 07.": "07-verification.md"}


def main():
    lines = io.open(GUIDE, encoding="utf-8").read().splitlines(keepends=True)
    blocks = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(`{3,})(\w*)\s*$", lines[i])
        if m:
            fence, lang = m.group(1), m.group(2)
            j = i + 1
            content = []
            while j < len(lines) and not re.match(r"^%s\s*$" % re.escape(fence), lines[j]):
                content.append(lines[j])
                j += 1
            blocks.append({"lang": lang, "text": "".join(content), "a": i + 1, "b": j})
            i = j + 1
        else:
            i += 1

    work = tempfile.mkdtemp(prefix="guide05-replay-")
    # Reader preconditions a fresh runner lacks: a git identity (without it
    # every scratch commit fails on a strict git and the approval gate grades
    # the APPROVAL file instead of the commit trailer the guide teaches) and
    # fixed dates for deterministic scratch commit ids.
    script = ["#!/bin/bash", 'SBE="%s"' % REPO, "cd %s" % work,
              'export GIT_AUTHOR_NAME="a reader" GIT_AUTHOR_EMAIL="reader@example.invalid"',
              'export GIT_COMMITTER_NAME="a reader" GIT_COMMITTER_EMAIL="reader@example.invalid"',
              'export GIT_AUTHOR_DATE="2026-07-30T00:00:00Z" GIT_COMMITTER_DATE="2026-07-30T00:00:00Z"']
    compare, marker, prev_bash = [], 0, None
    # The one forward reference a human resolves by reading ahead: "Write
    # 07-verification.md (next section) and run again" sits before the 07 block.
    seven = next((blk["text"] for blk in blocks
                  if blk["lang"] == "markdown" and blk["text"].startswith("# 07.")), None)
    wrote07 = False
    for i, blk in enumerate(blocks):
        lang, text = blk["lang"], blk["text"]
        first = text.splitlines()[0] if text.splitlines() else ""
        if lang == "bash":
            if first.startswith("SBE="):
                script.append("\n".join(text.splitlines()[1:]))
            else:
                if 'sbe_design.py" --strict' in text and not wrote07 and seven:
                    script.append("cat > 07-verification.md <<'SBEREPLAYEOF'\n%sSBEREPLAYEOF" % seven)
                    wrote07 = True
                run = text.rstrip("\n")
                if 'sbe_intake.py" design/my-project' in run:
                    # The guide states the answers in prose (n, n, y, n, none,
                    # feature); a human types them, this harness pipes them.
                    # SIXTH ANSWER ADDED 2026-09-02: sbe_intake.py gained H4's
                    # origin question ("Is this a feature or a defect?"), and its
                    # own tools/test_sbe_intake.py already feeds six answers. This
                    # harness still fed five, so the interview hit EOF, wrote
                    # nothing, and four separate output blocks in the guide read as
                    # drifted when the only thing wrong was the answer count here.
                    run = run.replace('python3 "$SBE/tools/sbe_intake.py" design/my-project',
                                      "printf 'n\\nn\\ny\\nn\\nnone\\nfeature\\n' | "
                                      'python3 "$SBE/tools/sbe_intake.py" design/my-project')
                marker += 1
                script.append('echo "===MARK %d START==="' % marker)
                script.append(run)
                script.append('echo "===MARK %d END==="' % marker)
                prev_bash = marker
            continue
        if lang == "markdown":
            fname = None
            for head, f in FILE_HEADS.items():
                if first.startswith(head):
                    fname = f
            if fname == "07-verification.md" and wrote07:
                prev_bash = None
                continue
            if first.startswith("## System context"):
                fname = "06-diagrams.md"
            if fname:
                script.append("cat > %s <<'SBEREPLAYEOF'\n%sSBEREPLAYEOF" % (fname, text))
            prev_bash = None
            continue
        if lang == "json":
            if '"answers"' in text:
                prev_bash = None
                continue
            fname = ("numbers-manifest.json" if '"figures"' in text else
                     "migration-receipt.json" if '"forward"' in text else
                     "ran-receipt.json" if '"checks"' in text else None)
            if fname:
                script.append("cat > %s <<'SBEREPLAYEOF'\n%sSBEREPLAYEOF" % (fname, text))
            prev_bash = None
            continue
        if lang:
            prev_bash = None
            continue
        if first.startswith("This change writes partner order data"):
            script.append("cat > APPROVAL <<'SBEREPLAYEOF'\n%sSBEREPLAYEOF" % text)
            prev_bash = None
            continue
        if prev_bash is not None:
            compare.append((i, prev_bash))
            prev_bash = None

    sh = os.path.join(work, "replay.sh")
    io.open(sh, "w").write("\n".join(script) + "\n")
    try:
        out = subprocess.run(["bash", sh], capture_output=True, text=True, timeout=300,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("the replay did not finish within 300s; a harness that hangs proves nothing")
        return 1
    caps = {int(m.group(1)): m.group(2) for m in
            re.finditer(r"===MARK (\d+) START===\n(.*?)===MARK \1 END===\n", out.stdout, re.S)}

    fails, patches = 0, []
    for i, mk in compare:
        want, got = blocks[i]["text"], caps.get(mk, "<NO CAPTURE>")
        # The same declared-volatile masks the book harness uses (paths, demo
        # shas, interpreter-version unittest ids, the merge-base line); one
        # definition, imported, so the two harnesses cannot drift apart.
        from replay_book import stable
        if stable(want) != stable(got):
            fails += 1
            print("=== BLOCK %d (lines %d-%d) DIFFERS ===" % (i, blocks[i]["a"], blocks[i]["b"]))
            for line in difflib.unified_diff(want.splitlines(), got.splitlines(),
                                             "guide", "live", lineterm="", n=1):
                print(line)
            patches.append((blocks[i]["a"], blocks[i]["b"], got))
            print()
    print("compared %d output blocks, %d differ" % (len(compare), fails))
    if WRITE and patches:
        for a, b, got in sorted(patches, reverse=True):
            lines[a:b] = [l if l.endswith("\n") else l + "\n" for l in got.splitlines(True)]
        io.open(GUIDE, "w", encoding="utf-8").write("".join(lines))
        print("patched %d blocks in place from live output" % len(patches))
    return 1 if (fails and not WRITE) else 0


if __name__ == "__main__":
    sys.exit(main())
