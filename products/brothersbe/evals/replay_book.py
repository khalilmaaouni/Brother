#!/usr/bin/env python3
"""Replay docs/book/[0-9][0-9]-*.md, and every other directory named in
CONTENT_DIRS below, against the live tools.

Mirror of evals/replay_guide05.py, generalized from one guide to every
chapter of the book. The book's own front matter (docs/book/00-cover.md)
makes the same claim guide-05 opened with: every terminal block in these
pages is real output, produced by actually running the command, and the
book's own build check re-executes each one and rejects the page if the
tool's live output ever drifts from what is printed here. This harness is
that check, made mechanical instead of asserted by hand.

Block annotation convention (the same one guide-05 uses): a fenced ```bash
block is a command, typed exactly as shown. When the very next fenced block
is bare, meaning its opening fence carries no language tag (``` with nothing
after it, not ```json or ```markdown or ```mermaid or any other tag), that
bare block is the literal stdout the command printed, compared byte for
byte. Any other fenced block in between, of any language, breaks the pairing:
it is not compared, because it is an artifact being shown (a file's
contents, a diagram, a config), not a claim about what a command printed.
The book's chapters need no synthetic-dossier setup the way the guide's
scratch engagement does: the worked estate under docs/book/estate/ is a
real, committed, runnable fixture, so a chapter's bash blocks are executed
directly, with the repository root as the working directory, exactly as a
reader sitting at the repo root would type them.

Each chapter file is replayed as its own independent script (a fresh
process, cwd reset to the repo root), so one chapter's state never leaks
into another's and a chapter can be read, and replayed, on its own.

Run:  python3 evals/replay_book.py            (report; exit 1 if any differ)
      python3 evals/replay_book.py --write    (patch stale blocks in place,
                                                from the live output, so no
                                                maintainer ever hand-types an
                                                expected block)

Wired into evals/run_evals.py as a docs-class eval, so drift fails the gate.
Standard library only, no network; commands run against the repository as
it stands on disk (the worked estate's generated files are gitignored and
regenerated deterministically, so replay never touches anything tracked).
"""
import difflib, io, os, re, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK_DIR = os.path.join(REPO, "docs", "book")
# The same NN-name.md, bash-plus-bare convention now also covers the
# role-specific "for-X" guides that need the identical honesty guarantee the
# book's front matter promises: a terminal example in one of these pages is
# real output, re-executed here, never hand-typed and never left to drift.
# Read as a plain list rather than discovered by globbing docs/for-*, so a
# new for-X directory joins this replay by a deliberate one-line edit, not by
# merely existing on disk.
CONTENT_DIRS = (BOOK_DIR, os.path.join(REPO, "docs", "for-business-analysts"))
# The zero-entry fixture the replay pins the hooks-wiring check to. Named once
# here because the harness uses it twice: to point a run at it (the export in
# replay_chapter) and to fold the resulting line back to a machine-neutral
# marker (stable, below). Renaming the fixture and moving only one of the two
# would leave the comparison broken in a way that reads as a content defect in
# the book rather than as the harness change it would be.
INSTALLED_PLUGINS_FIXTURE = os.path.join(
    REPO, "evals", "fixtures", "installed_plugins_none.json")
WRITE = "--write" in sys.argv

# --write REFUSES TO RUN FROM A THROWAWAY CHECKOUT, because it records whatever
# machine it ran on. This mode re-captures live output and patches it into
# shipped chapters, so every absolute path the tools print becomes documentation.
# Run from a temporary worktree, it wrote that worktree's own directory into the
# book: readers were shown a scratch path that no longer existed, presented as an
# installation. That reached main once before it was caught, and was then very
# nearly repeated by the same hand in the same session, which is why this is a
# refusal in the code and not a line in a checklist.
#
# The test is the path itself rather than a flag anyone can forget: a checkout
# under a temporary directory is not a place shipped content may be generated
# from. Reading and comparing are untouched; only --write is refused.
if WRITE:
    _real = os.path.realpath(REPO)
    for _tmp in (os.path.realpath(p) for p in ("/tmp", "/private/tmp", "/var/folders")):
        if _real == _tmp or _real.startswith(_tmp + os.sep):
            sys.stderr.write(
                "replay_book --write: refusing to regenerate shipped chapters from %s.\n"
                "This mode pastes live output, including absolute paths, into the book, so\n"
                "the directory it runs from becomes documentation. Run it from the primary\n"
                "checkout of the repository, never a temporary worktree.\n" % REPO)
            sys.exit(2)
# Overridable because wall-clock is a fact about the MACHINE, not the book:
# five concurrent sessions starved 4-second chapters past the old fixed cap,
# and a timeout counted as differing blocks that were never compared at all.
TIMEOUT_PER_CHAPTER = int(os.environ.get("SBE_REPLAY_TIMEOUT", "60"))

# One line of live output is repository-state-dependent by design: the status
# and impact tools print the merge-base diff line ("git diff <sha>..HEAD over
# N changed file(s)"), and the sha and the count move with every commit and
# every push. A book page cannot freeze that line without lying the moment the
# repository moves, so the pages that show it say so in prose, and this
# comparison treats exactly that substring, and nothing else, as declared
# volatile. Every other byte of every block is still compared literally.
VOLATILE_LINE = re.compile(
    r"git diff [0-9a-f]{7,40}\.\.HEAD over \d+ changed file\(s\)")

# The first authenticated read of the CI logs (2026-07-31) proved three more
# machine-dependent surfaces inside otherwise-honest excerpts, each masked
# here BY SHAPE and nothing else, so every remaining byte stays compared:
# absolute filesystem paths (the author's /Users/... against a runner's
# /home/runner/...), bare commit hashes of the demo repositories the replay
# itself creates (a commit id folds in dates and identity, so no two
# machines agree), and the unittest verbose id, whose format gained the
# method name in newer interpreters while the floor this project promises
# is 3.9.
# THE ROOT SEGMENTS BELOW ARE NOT A FIXED LIST, and were until 2026-08-26.
# They were written as Users and home because that is what a developer laptop
# and a GitHub runner use. A CONTAINER RUNNING AS ROOT uses neither: $HOME is
# /root, one segment deep, so no mask below ever reached a home-rooted path
# there and this whole comparison could not agree on any such machine,
# whatever the book said. Found in exactly that container, where
# 04-install-day block 1 differed only in the ~/.claude/skills/... token and
# was read for days as documentation drift rather than as an unmaskable
# machine.
#
# The running machine's OWN home root joins the list instead, so a home whose
# shape nobody anticipated (/root here, /data or /workspace elsewhere) gets
# the same anchor treatment as /Users and /home without another edit here.
# Derived, never hardcoded: adding "root" as a literal would fix this one
# container and leave the next one to be rediscovered the same slow way.
_ROOTS = ["Users", "home", "private/tmp", "tmp", "var/folders"]
_HOME_SEG = os.path.expanduser("~").strip("/").split("/")[0]
if _HOME_SEG and _HOME_SEG not in _ROOTS:
    _ROOTS.append(_HOME_SEG)
# The anchor heuristic below is scoped tighter than VOLATILE_PATH on purpose
# (see its own comment): only genuine HOME shapes, never the scratch roots.
_HOME_ROOTS = [r for r in _ROOTS if r not in ("private/tmp", "tmp", "var/folders")]

VOLATILE_PATH = re.compile(
    r"/(?:%s)/[^\s:;,)'\"]+" % "|".join(_ROOTS))

# VOLATILE_PATH above swallows an ENTIRE absolute path, root and every
# subdirectory after it, into one opaque marker. That is fine for a scratch
# directory (its whole shape is volatile), but it is too blunt for the
# checkout root: two machines only ever disagree on the ROOT, never on what
# a tool reports is inside it, and a tool that named the wrong file or the
# wrong subdirectory after a correct-looking root would hide behind the same
# marker as a genuinely portable line. This project's own book-portability
# defect (2026-08-19) was exactly that shape: the same checkout compared
# clean at one path and 2-differed at another, because the shipped book
# pages carry one author's absolute repository root verbatim.
#
# Two mechanisms close the gap, run in order, before VOLATILE_PATH:
#
# 1. An EXACT literal replace of REPO, this run's own known absolute root.
#    That is ground truth for whatever this run just captured as live
#    output, wherever the tree happens to live (a home directory, a CI
#    workspace, even a temporary worktree), with zero ambiguity: it can
#    only ever match REPO itself, never a scratch directory that merely
#    resembles it.
# 2. VOLATILE_REPO_ROOT, a heuristic for the OTHER side of the comparison:
#    a book page recorded on a different machine, whose absolute root this
#    run cannot know in advance. For a Users- or home-rooted path it prefers
#    masking ONLY the prefix up to, but not including, one of this
#    repository's own top-level entries (tools, docs, .sbe, bin, ...) when
#    one follows, leaving that entry name and everything after it exactly
#    as printed, so a wrong file or subdirectory still differs. The
#    boundary is found from what actually exists on disk under REPO right
#    now, never by counting characters or assuming a fixed depth. When no
#    such entry follows (a bare root with nothing after it, or a path that
#    never reaches one, such as the user's own ~/.claude or ~/.local/bin),
#    it falls back to masking the whole token with the SAME placeholder,
#    so a bare root agrees with what the exact replace above already did to
#    THIS run's own bare root, rather than one side leaving a literal path
#    where the other already folded it away.
#
#    This heuristic is deliberately scoped to Users/home and nothing wider:
#    --write above refuses to run from /tmp, /private/tmp, or /var/folders
#    precisely so a shipped book page can never carry a temp-rooted path, so
#    a book page's own root is always Users- or home-shaped. Widening the
#    same entry-name trick to /tmp paths was tried and reverted: a demo
#    estate a chapter builds there (/tmp/sbe-book-ch11-repo/.sbe/checks.yml,
#    for one) legitimately carries its own .sbe/, and matching that as if it
#    were THIS repository's .sbe would fold two different scratch directory
#    names into the same marker, exactly the "swallows a different
#    directory" failure this fix exists to avoid. /tmp and its kin are left
#    for VOLATILE_PATH below, unchanged from before this fix.
#
#    .claude is left out of the anchor set for the same reason: BrotherSBE
#    ships its own project-local .claude/, but .claude is ALSO the global,
#    per-user Claude Code config directory (~/.claude/skills,
#    ~/.claude/hooks, ...), so treating it as proof of "this is the repo
#    root" would misname an unrelated home-relative path as the checkout.
#    That path still folds to a placeholder either way (the precise branch
#    if a repo-named entry happens to follow, the fallback otherwise), and
#    either outcome is safe: the text is identical on every machine sharing
#    one $HOME regardless of where the repository itself lives, so no
#    genuine difference is ever hidden by it.
#    ONE ENTRY IS ADDED THAT DISK CANNOT SUPPLY, and leaving it out was a
#    live disagreement between the two branches above rather than a
#    theoretical gap. `.brother/config` is the repository opt-in file the
#    installer WRITES (E50), so it is absent from a checkout nobody opted in
#    and therefore absent from os.listdir here. Without it a live line reading
#    "<REPO>/.brother/config" kept its suffix (the exact replace folds only
#    REPO itself) while the book's own root swallowed the whole token including
#    the suffix, and the two sides differed on a line that says the same thing.
#    Any root-relative path the tools create rather than ship belongs in this
#    list for the same reason.
_CREATED_AT_REPO_ROOT = (".brother",)
_REPO_TOP_LEVEL = sorted(
    set(e for e in os.listdir(REPO) if e != ".claude") | set(_CREATED_AT_REPO_ROOT))
VOLATILE_REPO_ROOT = re.compile(
    r"/(?:%s)/(?:[^\s:;,)'\"]*?(?=/(?:%s)\b)|[^\s:;,)'\"]+)"
    % ("|".join(_HOME_ROOTS),
       "|".join(re.escape(e) for e in _REPO_TOP_LEVEL)))
# The same mask for Windows renderings of a path, so the comparison is
# platform-neutral: a drive-letter absolute path, and any token joined by
# backslashes (os.path.join on Windows). POSIX transcripts never contain
# these shapes, so the extra masks are inert there. Added after run
# 31040612827 showed every block differing on windows-latest only by
# path rendering.
VOLATILE_WINPATH = re.compile(r"[A-Za-z]:[\\/][^\s;,)'\"]+")
VOLATILE_WINREL = re.compile(r"\b[\w.-]+(?:\\[\w.*-]+)+")
VOLATILE_SHA = re.compile(r"\b[0-9a-f]{12,40}\b")
VOLATILE_TESTID = re.compile(r"\((__main__\.[A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*\)")
# 3.11 added fine-grained error locations: caret and tilde underline lines
# beneath a traceback frame. The floor is 3.9, whose tracebacks carry none,
# so an underline-only line is interpreter decoration, not content. The
# match takes the line's newline with it: stripping only the glyphs left a
# blank line behind on the side that had the underline, and a 3.9-recorded
# excerpt with no underline at all could never equal it (proven by the two
# 3.14 CI legs, 2026-07-31). A line that carries any byte besides
# underline glyphs and horizontal whitespace is content and never matches.
VOLATILE_CARETS = re.compile(r"^[ \t]*[\^~]+[ \t]*$\n?", re.M)
# The doctor's python line prints the LIVE interpreter version next to the
# floor: "python  PASS  3.9.6 (floor is 3.9)". The version is the machine's,
# so only that token masks; the verdict before it and the floor text after
# it stay compared byte for byte.
# The release version is volatile for exactly the reason the python version is:
# the book pastes real command output, and every release bump would otherwise
# rewrite six blocks across three chapters or block the merge that bumped it.
# Pinning it makes the book a hostage of the version file.
VOLATILE_VERSION = re.compile(r"\bv?\d+\.\d+\.\d+-rc\.\d+\b")

VOLATILE_PYVER = re.compile(
    r"^(python\s+(?:PASS|FAIL)\s+)\d+\.\d+\.\d+(?=\s*\(floor is )", re.M)

# The doctor's install-identity line counts how many OTHER copies of this
# plugin are installed elsewhere on the machine that ran the check: "N
# installed copies examined: M match this source at V, K are lagging
# installs at other versions". That count is the machine's install history,
# not the book's content, exactly the reasoning the python version and the
# release version above already mask: any unrelated `plugin install` on the
# author's machine moves N and K without the page's claim changing at all.
# The verdict word and the surrounding prose stay compared byte for byte;
# the three counts mask, and so does V, for the same reason the version
# masks below cover it everywhere else it is quoted.
VOLATILE_INSTALL_COUNT = re.compile(
    r"(^install-identity\s+PASS\s+)\d+( installed copies examined: )\d+"
    r"( match this source at )([^,]+)(, )\d+( are lagging installs)", re.M)

# THIS repository's own release number (VERSION / plugin.json's "version",
# e.g. "3.5.2") is pasted verbatim into four more shapes of live doctor and
# install-receipt output, none of which is book content: the plugin-manifest
# line, the doctor summary footer, the JSON install receipt's toolVersion
# field, and the hooks-wiring NO-DATA line naming the plugin cache entry it
# did not find, whose mask is defined separately below because the file that
# line names went volatile too. A version bump moves every one across every
# chapter that shows a doctor run or an install receipt for no content
# reason, exactly the class the last three re-pins (2026-08-28) belonged
# to, the same one VOLATILE_PYVER and VOLATILE_INSTALL_COUNT already close
# for the python version and the install counts. Each is anchored on the
# literal prose around it, so only the version folds away and a wrong
# verdict word, count, or path still differs.
VOLATILE_PLUGIN_MANIFEST_VERSION = re.compile(
    r"(^plugin-manifest\s+PASS\s+manifest )\S+(, VERSION )\S+", re.M)
# The hooks-wiring NO-DATA line names a SECOND volatile thing beside the
# version: the installed-plugins file the check actually consulted. That went
# volatile on 2026-08-29, when the replay began pinning the file to the fixture
# above so the recorded "no installed copy" scenario stopped depending on
# whatever happens to be installed on the replaying machine. A reader running
# `sbe doctor` reads ~/.claude/plugins/installed_plugins.json and the book
# records that; the replay reads the fixture, and the check honestly names what
# it read. Neither rendering is the book's content, so both fold to one marker.
#
# That same pin is what broke the version mask this replaces, which was
# anchored on the literal "~/.claude/plugins/" the live side had stopped
# printing: the version folded on the book side and stayed literal on the live
# side, one unfoldable line, and the whole of the three-block regression that
# then stood on main. A mask anchored on surrounding prose is coupled to that
# prose, so the anchor moves whenever the tool's wording does.
#
# The two legitimate renderings are listed BY NAME rather than matched as
# whatever token happens to sit there, so the line keeps its teeth: a third
# file named here, which is what a pin pointing somewhere unintended prints,
# still differs and is still caught, as do the verdict word, the counts, and
# every other byte of the line.
_FIXTURE_MASKED = "<repo-root>" + INSTALLED_PLUGINS_FIXTURE[len(REPO):]
VOLATILE_HOOKS_ENTRY = re.compile(
    r"(no brothersbe )\S+( entry in )"
    r"(?:~/\.claude/plugins/installed_plugins\.json|%s)"
    r"(, and SBE_HOOKS_JSON is unset)" % re.escape(_FIXTURE_MASKED))
VOLATILE_SBE_FOOTER_VERSION = re.compile(r"(^sbe )\S+(, evidence schema )", re.M)
VOLATILE_TOOL_VERSION = re.compile(r'("toolVersion":\s*")[^"]+(")')
# install.sh's own dry-run plan names the release tag it would look for
# (`tag="v$version"` in install.sh), the same release number under a fifth
# shape: "if tag v3.5.2 is published on <repo>.git".
VOLATILE_INSTALL_TAG = re.compile(r"(if tag v)\S+( is published on)")


def stable(text):
    """Mask the declared-volatile shapes before comparison. Idempotent."""
    text = VOLATILE_LINE.sub(
        "git diff <live-base>..HEAD over <live-count> changed file(s)", text)
    text = text.replace(REPO, "<repo-root>")
    text = VOLATILE_REPO_ROOT.sub("<repo-root>", text)
    text = VOLATILE_PATH.sub("<path>", text)
    text = VOLATILE_WINPATH.sub("<path>", text)
    text = VOLATILE_WINREL.sub("<path>", text)
    text = VOLATILE_SHA.sub("<sha>", text)
    text = VOLATILE_TESTID.sub(r"(\1)", text)
    text = VOLATILE_CARETS.sub("", text)
    text = VOLATILE_PYVER.sub(r"\1<python-version>", text)
    text = VOLATILE_VERSION.sub("<version>", text)
    text = VOLATILE_INSTALL_COUNT.sub(
        r"\g<1><n>\g<2><n>\g<3><version>\g<5><n>\g<6>", text)
    text = VOLATILE_PLUGIN_MANIFEST_VERSION.sub(r"\1<version>\2<version>", text)
    text = VOLATILE_HOOKS_ENTRY.sub(
        r"\1<version>\2<installed-plugins-json>\3", text)
    text = VOLATILE_SBE_FOOTER_VERSION.sub(r"\1<version>\2", text)
    text = VOLATILE_TOOL_VERSION.sub(r"\1<version>\2", text)
    text = VOLATILE_INSTALL_TAG.sub(r"\1<version>\2", text)
    return text


def chapters(content_dir=BOOK_DIR):
    return sorted(n for n in os.listdir(content_dir) if re.match(r"^\d{2}-.*\.md$", n))


def extract_blocks(text):
    """Same fence-walking approach as replay_guide05.py: find every fenced
    block (```lang ... ``` or ````lang ... ```` for fences that themselves
    contain triple backticks), keeping the language tag and the line range
    so a patch can be spliced back in by line number."""
    lines = text.splitlines(keepends=True)
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
    return lines, blocks


CAPABILITIES = {
    # A POSIX SHELL TOOLCHAIN, WHICH IS WHAT THESE TRANSCRIPTS ARE.
    # The chapters declaring this are recordings of a real POSIX session, and
    # they invoke `python3` by name 84 times. On Windows the interpreter is
    # `python.exe` and `python3` resolves to nothing, so every block failed at
    # once: that leg reported `compared 136 output blocks, 136 differ` in 1.28
    # seconds, where the same suite takes about eleven on a POSIX host. It was
    # never 136 content defects. It was one missing interpreter name.
    #
    # A SKIP HERE IS NO-DATA AND NAMES ITSELF, which is the only honest verdict
    # available: on that platform these transcripts cannot run as written, so
    # nothing was examined and nothing may be reported as matching.
    #
    # The two other repairs were considered and rejected on the record.
    # Rewriting the book to say `python` changes shipped documentation for every
    # reader to suit one platform, and on a host where `python` is Python 2 it
    # is actively wrong. Handing the harness a `python3` shim on Windows
    # manufactures a toolchain the machine does not have, which buys a pass by
    # lying about the machine.
    #
    # Detected by asking the host, never by naming a platform, so a POSIX box
    # that genuinely lacks `python3` also skips and also says why.
    #
    # Founder decision, 2026-08-08, recorded in the vault as
    # DECISION-2026-08-08-windows-book-chapters. What it costs is stated there
    # rather than implied: these transcripts are verified on Linux and macOS,
    # and are NOT verified on Windows. Verifying them there needs a
    # Windows-native transcript set captured on Windows, which is POST-V1.
    "posix": ("a POSIX shell toolchain where the interpreter is named python3",
              lambda: __import__("shutil").which("python3") is not None),
    "claude": ("the Claude Code CLI on PATH",
               lambda: __import__("shutil").which("claude") is not None),
    "gh": ("gh on PATH",
           lambda: __import__("shutil").which("gh") is not None),
    "vault": ("a wired BrotherSBE vault (BROTHERSBE_VAULT set and present)",
              lambda: bool(os.environ.get("BROTHERSBE_VAULT"))
              and os.path.isdir(os.environ.get("BROTHERSBE_VAULT", ""))),
}

_REQUIRES = re.compile(r"<!--\s*replay:\s*chapter\s+requires\s+([a-z]+)\s*-->")


def _chapter_requirements(text):
    """Capability names the whole chapter declares. Chapter blocks share one
    shell and one state: a skipped setup block would orphan every later
    excerpt built on it, so capability is a property of the CHAPTER."""
    return _REQUIRES.findall(text)


# The shape the tools use to refuse an unresolvable revision. Matched rather
# than assumed: bin/sbe impact answers "NO-DATA. base '<ref>' does not resolve
# in this repository".
_UNRESOLVED = re.compile(r"'([^']+)' does not resolve in this repository")


def replay_chapter(name, content_dir=BOOK_DIR):
    """Returns a dict for one chapter: lines, blocks, compare, fails, patches,
    and the capability-skipped blocks, each named.
    compare is a list of (block_index, marker) pairs; patches is a list of
    (a, b, got) ready to splice into lines if --write is set."""
    path = os.path.join(content_dir, name)
    lines, blocks = extract_blocks(io.open(path, encoding="utf-8").read())

    # The harness supplies the reader's own preconditions a fresh runner
    # lacks: a git identity (a reader following the book has one configured;
    # a CI runner does not, and without it every scratch commit fails and a
    # downstream gate grades the wrong artifact) and fixed dates, which also
    # make scratch commit ids deterministic across machines.
    script = ["#!/bin/bash", "cd %s" % REPO,
              'export GIT_AUTHOR_NAME="a reader" GIT_AUTHOR_EMAIL="reader@example.invalid"',
              'export GIT_COMMITTER_NAME="a reader" GIT_COMMITTER_EMAIL="reader@example.invalid"',
              'export GIT_AUTHOR_DATE="2026-07-30T00:00:00Z" GIT_COMMITTER_DATE="2026-07-30T00:00:00Z"',
              # A reader's own machine may or may not have brothersbe installed via the
              # marketplace, and if it does, whatever version happens to be cached there
              # is a fact about that machine, not about the book. `doctor`'s hooks-wiring
              # check resolves "is there an installed copy" from
              # ~/.claude/plugins/installed_plugins.json by default, so without this pin
              # the recorded NO-DATA block (docs/book/15-the-platform-lead-deep-dive.md
              # BLOCK 16) starts reading PASS the moment this checkout's shipped version
              # happens to match something in that cache, with no book change and no
              # commit involved: see docs/plan/FINDING-book-replay-version-coupling-2026-08-28.md
              # in the Brother umbrella repo. SBE_INSTALLED_PLUGINS_JSON pins the replay to
              # a fixture with zero brothersbe entries, so the recorded "no installed copy"
              # scenario is a property of the fixture, never of the replaying machine.
              'export SBE_INSTALLED_PLUGINS_JSON="%s"' % INSTALLED_PLUGINS_FIXTURE]
    unmet = [r for r in _chapter_requirements("".join(lines))
             if r in CAPABILITIES and not CAPABILITIES[r][1]()]
    if unmet:
        # A capability this machine lacks: the whole chapter is SKIPPED and
        # COUNTED, never silently, because a comparison that never ran must
        # not read as one that matched, and a half-run chapter would compare
        # excerpts against state their setup never built.
        n_comparable = sum(1 for j, blk2 in enumerate(blocks)
                           if blk2["lang"] == "" and j > 0
                           and blocks[j - 1]["lang"] == "bash")
        return lines, blocks, [], 0, [], [(name, n_comparable, unmet)]

    compare, marker, prev_bash = [], 0, None
    skipped = []
    for i, blk in enumerate(blocks):
        lang, text = blk["lang"], blk["text"]
        if lang == "bash":
            marker += 1
            script.append('echo "===MARK %d START==="' % marker)
            script.append(text.rstrip("\n"))
            script.append('echo "===MARK %d END==="' % marker)
            prev_bash = marker
            continue
        if lang == "":
            if prev_bash is not None:
                compare.append((i, prev_bash))
            prev_bash = None
            continue
        # Any other tagged block (markdown, json, mermaid, python, ...) is
        # an artifact being shown, not a command's output: it breaks the
        # pairing without itself being run or compared.
        prev_bash = None

    if marker == 0:
        return lines, blocks, [], 0, [], skipped

    # newline="\n" pins the write to a bare LF regardless of platform. The
    # default (newline=None) translates every '\n' written to os.linesep,
    # which is '\r\n' on Windows: the generated script's own commands then
    # carry a trailing \r baked into their arguments (a path, a date, a
    # heredoc line), and bash echoes that \r back inside the captured output
    # instead of failing outright. That is not a captured-output encoding
    # difference, so the comparison's own newline normalization can never
    # catch it: the corruption is in what the command was TOLD to do, not in
    # how its answer was later read. A fixture writer left in text mode was
    # exactly the same defect family this project already fixed once before.
    sh_path = os.path.join(BOOK_DIR, ".replay-%s.sh" % name.replace("/", "_"))
    io.open(sh_path, "w", newline="\n").write("\n".join(script) + "\n")
    try:
        out = subprocess.run(["bash", sh_path], capture_output=True, text=True,
                             timeout=TIMEOUT_PER_CHAPTER, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("%s: the replay did not finish within %ds; a harness that hangs proves nothing"
              % (name, TIMEOUT_PER_CHAPTER))
        return lines, blocks, compare, len(compare), [], skipped
    finally:
        # missing_ok: a concurrent tidy-up sweeping .replay-*.sh litter can
        # win the race to this unlink; the script already ran, so a missing
        # temp is a fact to tolerate, not an error to raise.
        import pathlib
        pathlib.Path(sh_path).unlink(missing_ok=True)

    caps = {int(m.group(1)): m.group(2) for m in
            re.finditer(r"===MARK (\d+) START===\n(.*?)===MARK \1 END===\n", out.stdout, re.S)}

    fails, patches = 0, []
    for i, mk in compare:
        want, got = blocks[i]["text"], caps.get(mk, "<NO CAPTURE>")
        if stable(want) != stable(got):
            fails += 1
            print("=== %s BLOCK %d (lines %d-%d) DIFFERS ===" % (name, i, blocks[i]["a"], blocks[i]["b"]))
            # A VANISHED PINNED COMMIT NAMES ITSELF, because it does not look
            # like a drift and reads like one. Chapters pin commit ranges so a
            # worked example gives the same answer on every clone, but a
            # rewritten history takes those commits out of the object database
            # and the tools then answer NO-DATA, correctly, for every reader.
            # The raw diff of that shows a page full of expected output against
            # nothing, which is indistinguishable at a glance from a tool whose
            # output changed. It cost one session hours in 2026-08-26 before
            # anyone checked whether the commits still existed, so the check is
            # here rather than in whoever reads this next.
            if _UNRESOLVED.search(got):
                for ref in _UNRESOLVED.findall(got):
                    print("!!! %s pins '%s', which does not resolve in this "
                          "repository. This is NOT drift in what the tools "
                          "print: the commit is gone, most likely to a history "
                          "rewrite. Re-pin the range in the chapter to two "
                          "commits that exist; do not regenerate the block."
                          % (name, ref))
                print()
            for line in difflib.unified_diff(want.splitlines(), got.splitlines(),
                                             "book", "live", lineterm="", n=1):
                print(line)
            patches.append((blocks[i]["a"], blocks[i]["b"], got))
            print()
    return lines, blocks, compare, fails, patches, skipped


def main():
    total_compared, total_fails = 0, 0
    all_skipped = []
    for content_dir in CONTENT_DIRS:
        if not os.path.isdir(content_dir):
            continue
        for name in chapters(content_dir):
            lines, blocks, compare, fails, patches, skipped = replay_chapter(
                name, content_dir)
            all_skipped.extend(skipped)
            total_compared += len(compare)
            total_fails += fails
            if WRITE and patches:
                for a, b, got in sorted(patches, reverse=True):
                    lines[a:b] = [l if l.endswith("\n") else l + "\n" for l in got.splitlines(True)]
                path = os.path.join(content_dir, name)
                io.open(path, "w", encoding="utf-8").write("".join(lines))
                print("%s: patched %d block(s) in place from live output" % (name, len(patches)))
    for name, n_blocks, unmet in all_skipped:
        for cap in unmet:
            print("SKIPPED chapter %s (%d comparable block(s)): requires %s (%s), which "
                  "this machine lacks; comparisons that never ran are counted here, "
                  "never as matches" % (name, n_blocks, cap, CAPABILITIES[cap][0]))
    print("compared %d output blocks, %d differ" % (total_compared, total_fails))
    return 1 if (total_fails and not WRITE) else 0


if __name__ == "__main__":
    sys.exit(main())
