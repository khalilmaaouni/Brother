#!/usr/bin/env python3
"""export_public: the single route from the private hub to the public
export target. docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 4, the
mechanical half of the founder's "Private hub, public export" ruling
(docs/decisions/2026-08-30-edition-architecture.html).

WHAT IT DOES, in order:

  1. Reads docs/plan/EXPORT-ALLOWLIST.txt. A path not listed there never
     leaves the hub. NO-DATA (refuses) if the allowlist is missing: an
     exporter that cannot read its own law must not export by default.

  2. Builds the CANDIDATE EXPORT TREE: only the listed paths, copied from
     the hub root into a throwaway ORPHAN git commit (no parent, no
     inherited history). editions/ and any .brother-edition file are
     excluded by this code even if the allowlist somehow named them; that
     boundary is hard, never merely a line in a text file. Last, every
     product's own CHECKSUMS.sha256 that landed in the tree is regenerated
     over exactly the files this export copied (regenerate_product_
     manifests), so the shipped manifest never disagrees with the shipped
     bytes the way a verbatim copy of the hub's own, wider manifest would.

     ORPHAN ON PURPOSE. The gates below include cleanse.sh, which scans
     `git log -p --all`, every commit the repository has ever held. Building
     the candidate on top of the REAL public repository's fetched history
     would make that scan re-read hundreds of already-public commits on
     every single run, and a term already sitting in ALREADY-PUBLIC history
     (shipped before this exporter existed, unfixable without the history
     rewrite this estate's own law forbids doing casually) would then REFUSE
     EVERY FUTURE EXPORT FOREVER, which protects nothing new and blocks
     everything. The orphan commit scopes every gate to exactly what THIS
     export would newly send: the working tree the allowlist built. Measured
     on this hub's own tree 2026-08-30: fetching the real tip first put 730
     historical commits in scope and cleanse.sh refused on a term already
     public before this file existed.

     THE ALLOWLIST AND DENYLIST ARE THE ONLY FILTERS. Every `git add` this
     module runs passes -f (force): a file the allowlist walk copied into
     the candidate tree must never be silently dropped by a COPIED
     .gitignore obeying rules written for the hub, not for what this export
     decided to carry. Measured 2026-09-02: two tracked CSV fixtures, force-
     added past the hub's own root .gitignore's `*.csv` line, were copied by
     the allowlist walk, scanned by every gate, and listed in the
     regenerated CHECKSUMS.sha256, then silently left out of the commit by
     a plain `git add -A`, which obeyed that same copied .gitignore. The
     manifest is generated over exactly the files build_export_tree copied
     (regenerate_product_manifests); the commit must be that same tree,
     never a narrower one a stale ignore rule quietly re-filtered.

  3. Runs scripts/cleanse.sh, scripts/identity_guard.py and
     scripts/private_terms_scan.py against the CANDIDATE EXPORT TREE, never
     against the hub. All three read ONE private-term list, handed to each
     through BROTHER_PRIVATE_TERMS (E37, 2026-09-03): the environment's
     own value when set, else the file the estate's law names
     (DEFAULT_TERMS_FILE below). A list that is absent, empty or
     comment-only makes every gate read NO-DATA, never PASS. ANY nonzero
     exit from any of the three, FAIL or NO-DATA alike, refuses the export: a check that could not run has not
     certified the tree, and this exporter treats "I could not tell" and
     "it is fine" as the same non-pass, exactly as pre_push_gate.py already
     does for the hub's own boundary.

  4. Prints every gate's own verdict, and the count of paths it copied
     (what it WOULD export), whether or not the gates cleared.

  5. dry-run (the default): stops here.
     --push: requires --remote. Fetches the remote's current tip into a
     SEPARATE temp directory, rebuilds the identical allowlisted content
     as a new commit ON TOP of that tip (an honest append, not the orphan
     used for gating), sets BROTHER_EXPORT_INVOCATION=export_public.py (and
     prints that it did) for every subprocess call that pushes, and pushes
     that commit to its OWN branch (release/<version> for a tagged
     release, export/<commit> otherwise) with a plain (non-force) push,
     then opens a pull request against <branch> with gh and merges it.
     THE PULL REQUEST IS NOT CEREMONY: since the ruleset of row E64 the
     public repository requires a pull request on main, refuses non
     fast-forward and deletion, and grants no bypass, so the direct push
     this exporter used to make is refused with GH013 and the honest route
     is the one a contributor takes. NEVER --force at any step: an append
     that is not a fast-forward is a design assumption that broke, not a
     reason to overwrite history. The gates are NOT re-run on this second tree: its
     file content is byte-identical to the orphan already gated in step 3,
     only its git parent differs. --bootstrap (with --push only) is the
     one exception to the append rule: when the remote has no branch at
     all, this export becomes its first commit instead of refusing. A
     remote with any branch, of any name, still refuses --bootstrap
     outright, so the flag only ever opens the empty-repository case the
     2026-09-03 clean-extraction decision created, never a route to start
     unrelated history over a populated one. --tag (with --push only)
     adds one annotated tag on the MERGED tip of <branch>, and before
     anything is pushed the export tree must clear tag_time_checks: the
     release note it carries is really stamped (no placeholder Source
     revision block), every product's own scripts/verify-install.sh passes
     on it (which is what proves the regenerated manifests describe the
     shipped bytes), its own scripts/readiness_gate.py reads READY there
     (row E67: a fresh clone of v1.0.1 read NOT READY while the hub read
     ready), every relative markdown link in it resolves to a file it
     really carries, and every command the README names as its own proof
     runs there (row E70: `python3 scripts/test_battery_verdict.py`, the
     README's own proof, died on a docs/plan file the allowlist never
     carried).

Python 3, standard library only. No network beyond git's own fetch/push.
"""
import argparse
import difflib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edition_guard  # noqa: E402

ROOT = os.path.dirname(HERE)
DEFAULT_ALLOWLIST = os.path.join(ROOT, "docs", "plan", "EXPORT-ALLOWLIST.txt")
#: The value allowlist for the secret gate lives in pre_push_gate.py (one
#: source, so the hub's own pre-push scan and this export gate never drift
#: apart); it is re-exported here for readers and tests of this module.
from pre_push_gate import KNOWN_PUBLIC_EXAMPLE_VALUES, strip_public_examples  # noqa: E402
#: M6: exact paths withheld from an otherwise-allowlisted products/
#: directory (docs/plan/EXPORT-DENYLIST.txt explains each one by name).
#: Optional: a repository with nothing to withhold need not carry this
#: file, and its absence is not NO-DATA the way a missing allowlist is,
#: because an empty denylist changes nothing about what gets exported.
DEFAULT_DENYLIST = os.path.join(ROOT, "docs", "plan", "EXPORT-DENYLIST.txt")
DEFAULT_REMOTE = "https://github.com/khalilmaaouni/Brother"
DEFAULT_BRANCH = "main"
AUTHOR_NAME = "Khalil Maaouni"
AUTHOR_EMAIL = "khalilmaaouni@users.noreply.github.com"
COMMIT_MESSAGE = "export: allowlisted files from the private hub"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NODATA = 2

#: E6.1a: an external release-integrity reviewer confirmed the exporter's
#: reproducibility claim (scripts/reproduce_export.py rebuilds the export
#: byte for byte from a hub revision) but found that revision shipped
#: nowhere: not in the git tag, not in docs/releases/<version>.md. They had
#: to reconstruct it by hashing a file across candidate commits. This header
#: marks the section this exporter stamps into the release record at
#: release time, so a reader needs only what is already printed there.
SOURCE_REVISION_HEADER = "## Source revision"

#: The text this exporter writes into a release note's Source revision
#: section before a real stamp lands (scripts/cut_v1.0.0.sh and friends
#: seed release notes with this placeholder). Its presence means the
#: section is UNSTAMPED, even though SOURCE_REVISION_HEADER is already
#: there, so stamp_source_revision must not mistake it for a real stamp.
SOURCE_REVISION_PLACEHOLDER = "Stamped by the exporter at release time."

#: Excluded even if the allowlist names them, because the boundary is hard,
#: never merely a line in a text file a session could mis-edit.
HARD_EXCLUDE = {"editions", edition_guard.MARKER_FILE}

#: M6, task 2 (docs/plan/ONE-REPO-TRANSITION-2026-08-31.md, M6 row): the
#: hub's own root PROJECT.md describes the PRIVATE hub ("THE PRIVATE HOME of
#: all Brother development... All sessions work HERE") and names the hub's
#: internal 118 check battery as this repository's own checks, which is true
#: in the hub and false in public. Rather than maintain two meanings for one
#: filename by hand, the export swaps in a public-true variant for exactly
#: the listed destination paths, after the ordinary allowlist copy above.
#: Never invents a destination the allowlist did not already bring into the
#: tree: an override with no matching copied path is silently a no-op.
EXPORT_OVERRIDES = {
    "PROJECT.md": os.path.join("docs", "plan", "PROJECT.public.md"),
}

#: The product tool that computes both published figures below: the citation
#: inventory's coverage and the silent-failure lint's counts. A product that
#: does not ship it has no figures for the export to recompute.
PRODUCT_SCORER_REL = os.path.join("tools", "sbe_score.py")

#: The sentence in a product's SKILL.md that states its own lint run. Written
#: as the product's own eval reads it (`(\w+)`, so a spelled-out number and a
#: digit are both matched), because the eval recomputes these two numbers from
#: a live run and the export must hand it a sentence about the exported tree.
LINT_FIGURE_RE = re.compile(
    r"(\w+) waived hits and (\w+) files that were scanned and genuinely "
    r"found clean")

#: Every markdown inline link, `[text](target)`. Reference style links and
#: bare URLs are out of scope on purpose: the dead links row E70 names
#: (three README pages absent from the public tag) are all inline links.
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")

#: The shape of a README "prove it" command: python3 scripts/test_*.py,
#: whether it sits in a fenced block or inline in a sentence.
README_PROVE_RE = re.compile(r"python3 scripts/test_[A-Za-z0-9_]+\.py")

#: The export tree's own readiness gate, run at tag time from the export
#: tree's root exactly as a fresh clone would run it.
READINESS_GATE_REL = os.path.join("scripts", "readiness_gate.py")

#: The GitHub CLI. Since the ruleset of row E64 (pull request required on
#: main, non fast-forward and deletion refused, no bypass) a direct push to
#: main is refused with GH013, so the release route runs through a pull
#: request and this is the tool that opens and merges it. Named here so a
#: test can point an injected runner at a stand-in.
GH_BIN = "gh"

#: The branch a release is pushed to before its pull request. The tag is
#: cut from the MERGED tip of the protected branch, never from this one.
RELEASE_BRANCH_PREFIX = "release/"
EXPORT_BRANCH_PREFIX = "export/"


def load_allowlist(path=None):
    """Every non-blank, non-comment line: the exact root paths that may
    leave the hub. Returns None when the file is absent, never an empty
    list standing in for it: a missing law must refuse everything, not
    silently export nothing forever while claiming success."""
    path = path or DEFAULT_ALLOWLIST
    if not os.path.isfile(path):
        return None
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)
    return entries


def load_denylist(path=None):
    """Every non-blank, non-comment line: exact paths withheld even though
    a broader allowlist entry would otherwise carry them in. Unlike the
    allowlist, an ABSENT denylist is an empty list, not a refusal: there is
    nothing wrong with a repository state that has nothing to withhold."""
    path = path or DEFAULT_DENYLIST
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().strip("/")
            if line and not line.startswith("#"):
                entries.append(line)
    return entries


def _run(cmd, cwd, env=None, timeout=120):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               env=env, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        class _Fake:
            returncode = 1
            stdout = ""
            stderr = str(exc)
        return _Fake()


def signing_configured(cwd, run=None):
    """(signed, key_id): whether this checkout is ready for `git tag -s`.
    user.signingkey must be non-empty, and either gpg.format is set (an ssh
    key, or an explicit declaration) or a gpg secret key matching it is
    actually present, so a leftover signingkey line with no working key
    behind it does not read as ready. Row S5 (roadmap) is founder gated: no
    session sets any of this, it only reads what is already there."""
    run = run or _run
    key = run(["git", "config", "--get", "user.signingkey"], cwd)
    key_id = (key.stdout or "").strip()
    if key.returncode != 0 or not key_id:
        return False, ""
    fmt = run(["git", "config", "--get", "gpg.format"], cwd)
    if (fmt.stdout or "").strip():
        return True, key_id
    gpg_bin = shutil.which("gpg") or shutil.which("gpg2")
    if not gpg_bin:
        return False, key_id
    have = run([gpg_bin, "--list-secret-keys", key_id], cwd)
    if have.returncode == 0 and (have.stdout or "").strip():
        return True, key_id
    return False, key_id


def hub_head_rev(root=ROOT):
    """The hub's own current commit: the exact source revision an export
    built from `root` right now is cut from. None when this checkout has
    no readable git history, NO-DATA rather than a crash."""
    proc = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def stamp_source_revision(notes_path, version, source_rev):
    """Write the hub commit a release was cut from into its own release
    record, in place, so a reader can run reproduce_export.py using only
    values already printed in docs/releases/<version>.md, never private
    archaeology. Never overwrites a REAL stamp already present (a hand
    written pin, or an earlier real stamp, stands as is); the seeded
    SOURCE_REVISION_PLACEHOLDER block is not a stamp and gets replaced.
    Returns True on a write, False on any reason not to write: no such
    file, unreadable, or a real revision already stamped."""
    if not source_rev or not os.path.isfile(notes_path):
        return False
    try:
        with open(notes_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    block = (
        "%s\n\n"
        "Cut from hub commit `%s`. Reproduce the export byte for byte "
        "with:\n\n"
        "    python3 scripts/reproduce_export.py --source-rev %s --tag "
        "v%s --public <local checkout of this repository>\n"
        % (SOURCE_REVISION_HEADER, source_rev, source_rev, version)
    )
    if SOURCE_REVISION_HEADER in text:
        if SOURCE_REVISION_PLACEHOLDER not in text:
            return False  # a real stamp already stands, leave it
        placeholder_block = "%s\n\n%s\n\n" % (
            SOURCE_REVISION_HEADER, SOURCE_REVISION_PLACEHOLDER)
        if placeholder_block not in text:
            return False  # unexpected shape near the header, don't guess
        new_text = text.replace(
            placeholder_block, block.rstrip("\n") + "\n\n", 1)
    else:
        title, _, rest = text.partition("\n")
        new_text = title + "\n\n" + block.rstrip("\n") + "\n\n" + rest.lstrip("\n")
    with open(notes_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return True


def _tracked_files_under(root, rel):
    """Every path `git ls-files` reports at or under `rel` (root-relative,
    no leading slash), root-relative, in the order git prints them. [] when
    `rel` names nothing the index tracks (untracked file, untracked or
    nonexistent directory) or `root` is not a git repository at all: the
    hub TRACKS the files this exporter ships, so an untracked path is never
    invented into the export tree, the same "skipped, never invented" rule
    this function replaces at the filesystem-walk layer. Measured 2026-09-
    03: a raw filesystem walk (the previous shutil.copytree/copy2 approach)
    copied every gitignored build artifact and runtime file physically
    present under an allowlisted directory (__pycache__/*.pyc, .sbe/
    tasks.json and friends) and depended on `git add` in the CANDIDATE tree
    to silently re-filter them by a COPIED .gitignore; once that add was
    forced (-f, so a hub file force-tracked past its own ignore rule would
    still ship), the dependency broke and all of it leaked through. `git
    ls-files` is the hub's own authority on what it tracks, ignore rules
    included or overridden exactly as the hub itself resolved them at
    `git add` time (the two CSV fixtures are tracked despite `*.csv`
    because someone force-added them in the hub; ls-files reports them
    same as any other tracked path)."""
    proc = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", rel],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        return []
    return [p for p in proc.stdout.split("\0") if p]


def build_export_tree(dest, allowlist, root=ROOT):
    """Copy every HUB-TRACKED file under an allowlisted path from `root`
    into `dest` (see _tracked_files_under: `git ls-files` is the authority,
    never a raw filesystem walk). A listed path that names nothing tracked
    is skipped, never invented. Last step: regenerate_product_manifests
    (dest), so any product's own CHECKSUMS.sha256 that landed in `dest`
    describes the files that actually landed there, not the full hub tree
    it was generated over. Returns the list of allowlist entries that
    contributed at least one file."""
    copied = []
    for rel in allowlist:
        rel = rel.strip("/")
        if not rel or rel in HARD_EXCLUDE or rel.startswith("editions/"):
            continue  # the hard boundary, regardless of what the list says
        tracked = _tracked_files_under(root, rel)
        if not tracked:
            continue
        for tracked_rel in tracked:
            dst = os.path.join(dest, tracked_rel)
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.copy2(os.path.join(root, tracked_rel), dst)
        copied.append(rel)
    # The denylist is an INPUT of the tree being built, exactly as the
    # allowlist is, so it is read from `root` and never from whatever this
    # checkout carries today. Measured 2026-09-04 by lane X7-FIX: a rebuild
    # of an older revision (reproduce_export.py --source-rev) took its
    # allowlist from the revision and its denylist from the current
    # checkout, so a path denied today was withheld from a rebuild of a
    # release that shipped it, and the reproduction read a mismatch that
    # was never in the release. The relative path comes off the module's
    # own constant rather than being typed again, so when root is ROOT this
    # is byte for byte the old call.
    for deny_rel in load_denylist(os.path.join(
            root, os.path.relpath(DEFAULT_DENYLIST, ROOT))):
        deny_path = os.path.join(dest, deny_rel)
        if os.path.isdir(deny_path):
            shutil.rmtree(deny_path)
        elif os.path.isfile(deny_path):
            os.remove(deny_path)
        else:
            continue  # the allowlist never brought this path in; no-op
        if deny_rel in copied:
            copied.remove(deny_rel)
    for dest_rel, override_rel in EXPORT_OVERRIDES.items():
        dest_path = os.path.join(dest, dest_rel)
        if not os.path.exists(dest_path):
            continue  # the allowlist never brought this path in; no-op
        override_src = os.path.join(root, override_rel)
        if os.path.isfile(override_src):
            shutil.copy2(override_src, dest_path)
    regenerate_product_manifests(dest)
    # The manifests come first because the figure regeneration below reads
    # each product's CHECKSUMS.sha256 as its shipped-file list, exactly the
    # way that product's own citation check does; then the manifest of any
    # product whose figures actually moved is rebuilt over the new bytes.
    changed = regenerate_published_figures(dest)
    if changed:
        regenerate_product_manifests(dest, only=changed)
    return copied


def regenerate_product_manifests(dest, only=None):
    """Every product's own CHECKSUMS.sha256 (products/<name>/docs/
    RELEASE.md step 4: "so no version of the tree ships a manifest that
    disagrees with its own files") is generated by hashing every file the
    FULL hub tree tracks for that product, including ci/, release-control/,
    design/, program/ and the docs subdirectories the allowlist
    deliberately withholds. Copied verbatim by build_export_tree above,
    that manifest names hundreds of files this export never ships,
    breaking its own stated invariant the moment it crosses the export
    boundary. Regenerate each one IN PLACE over exactly the files
    build_export_tree just copied, with the product's OWN
    scripts/checksums.sh (so the manifest keeps the exact format that
    product's own verify-install.sh reads), never a hand rolled hasher
    here.

    checksums.sh prefers `git ls-files` whenever it can find a `.git`
    walking up from its own directory. Every caller of build_export_tree
    has already `git init`-ed (or fetched a real checkout into) `dest`
    before reaching here (build_orphan_commit, build_identity_check_dir,
    push_appended), so an unguarded run would read THAT outer repository's
    index rather than the bytes actually sitting in this product's exported
    directory: empty before the caller's own `git add`, or the stale tip of
    a previously pushed release in push_appended's case, neither of which
    is "the files actually exported" this run. GIT_CEILING_DIRECTORIES=dest
    stops checksums.sh's own `git rev-parse --is-inside-work-tree` probe
    from ever finding that outer .git, so the script takes its OWN
    documented fallback (a plain filesystem walk) every time, hashing
    exactly what build_export_tree just put on disk regardless of what
    `dest`'s throwaway repository does or does not have staged yet.

    `only`, when given, is the set of product directories to rebuild rather
    than all of them: regenerate_published_figures rewrites two documents
    after this function has already run once, and only the products it
    actually touched need their manifest rebuilt over the new bytes.

    Best effort: a checksums.sh that fails is named, loudly, and the stale
    manifest ships as is rather than the whole export crashing over one
    product's regeneration."""
    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = dest
    for base, dirs, files in os.walk(dest):
        if ".git" in dirs:
            dirs.remove(".git")
        if "CHECKSUMS.sha256" not in files:
            continue
        if only is not None and base not in only:
            continue
        generator = os.path.join(base, "scripts", "checksums.sh")
        if not os.path.isfile(generator):
            continue  # no generator shipped alongside this manifest here
        rel = os.path.relpath(base, dest)
        proc = _run(["bash", generator, "CHECKSUMS.sha256"], base, env=env)
        if proc.returncode != 0:
            text = ((proc.stdout or "") + (proc.stderr or "")).strip()
            verdict = text.splitlines()[-1] if text else "(no output)"
            print("export: FAILED to regenerate %s/CHECKSUMS.sha256 (exit "
                  "%s, %s); the stale, pre-export manifest ships as is"
                  % (rel, proc.returncode, verdict))
            continue
        with open(os.path.join(base, "CHECKSUMS.sha256"),
                  encoding="utf-8") as fh:
            count = sum(1 for _ in fh)
        print("export: regenerated %s/CHECKSUMS.sha256 over the export "
              "tree (%d file(s))" % (rel, count))


def _load_product_module(path, modname):
    """Import a product's own tool from the EXPORT tree by path, or None
    with the reason printed. Loaded from the export copy on purpose: the
    figures below must be computed by the code that ships beside them, and
    the lint's own self-skip resolves against the file it is scanning.

    The except is broad because executing another tool's module can raise
    anything its own import path raises; every one of them means the same
    thing here (this product's figures cannot be recomputed) and none of
    them may take the whole export down, so the reason is named and the
    caller moves on."""
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        print("export: cannot load %s, so its published figures stay as "
              "they were generated over the hub" % path)
        return None
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ in the export tree: importing a module from `dest`
    # writes bytecode NEXT TO IT by default, and the first run of this step
    # shipped five .pyc files the hub never tracked into the export.
    saved_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 - named below, never swallowed
        print("export: FAILED to load %s (%s: %s); its published figures "
              "stay as they were generated over the hub"
              % (path, type(e).__name__, e))
        return None
    finally:
        sys.dont_write_bytecode = saved_bytecode
    return module


def _shipped_markdown(base):
    """Every markdown file a product ships, derived the way that product's
    own citation check derives its scanned set: the CHECKSUMS.sha256
    manifest IS the shipped-file list when there is one, otherwise every
    .md under the root with hidden directories skipped. Returns a list of
    paths, empty when nothing could be read."""
    manifest = os.path.join(base, "CHECKSUMS.sha256")
    rels = []
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split(None, 1)
                    if len(parts) == 2 and parts[1].strip().endswith(".md"):
                        rels.append(parts[1].strip())
        except OSError as e:
            print("export: %s/CHECKSUMS.sha256 could not be read (%s), so "
                  "the shipped markdown set falls back to a walk"
                  % (os.path.basename(base), type(e).__name__))
            rels = []
    if rels:
        return [os.path.join(base, rel) for rel in sorted(rels)
                if os.path.isfile(os.path.join(base, rel))]
    docs = []
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in sorted(fns):
            if fn.endswith(".md"):
                docs.append(os.path.join(dp, fn))
    return docs


def prune_citation_inventory(base, module):
    """Drop every entry in this product's docs/CITATIONS.md whose URL no
    document the EXPORT tree ships still cites, and return whether the file
    changed.

    Why: the inventory is generated over the hub, where design/, program/
    and the internal docs the allowlist withholds do the citing. Shipped
    verbatim into an export carrying a subset, every entry those withheld
    documents were the only citers of becomes a stale entry, and this
    product's own citation-inventory check FAILs at gate severity in every
    public clone. The URLs are extracted by the product's OWN _doc_urls, so
    comment stripping and punctuation trimming can never drift from the
    check that reads the result.

    Refuses to write on partial evidence: an unreadable document or an
    empty URL set leaves the inventory exactly as it was, because pruning
    against a scan that failed would delete entries a reader still needs."""
    inv = os.path.join(base, "docs", "CITATIONS.md")
    if not os.path.isfile(inv):
        return False
    doc_urls = getattr(module, "_doc_urls", None)
    if doc_urls is None:
        print("export: %s ships no _doc_urls, so docs/CITATIONS.md stays as "
              "it was generated over the hub" % os.path.basename(base))
        return False
    inv_norm = os.path.normcase(os.path.normpath(inv))
    urls = set()
    for path in _shipped_markdown(base):
        if os.path.normcase(os.path.normpath(path)) == inv_norm:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as e:
            print("export: %s could not be read (%s), so docs/CITATIONS.md "
                  "stays as it was rather than being pruned against a scan "
                  "that missed a document" % (path, type(e).__name__))
            return False
        urls.update(doc_urls(body))
    if not urls:
        print("export: no shipped document under %s cites an external URL, "
              "so docs/CITATIONS.md stays as it was: an empty scan is not "
              "evidence that every entry is stale" % base)
        return False
    try:
        with open(inv, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as e:
        print("export: %s could not be read (%s), so it stays as it was"
              % (inv, type(e).__name__))
        return False
    heads = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if not heads:
        return False
    kept, dropped = list(lines[:heads[0]]), []
    bounds = heads + [len(lines)]
    for n, start in enumerate(heads):
        block = lines[start:bounds[n + 1]]
        url = block[0][3:].strip().rstrip(".,;:")
        if url in urls:
            kept.extend(block)
        else:
            dropped.append(url)
    rel = os.path.relpath(base, os.path.dirname(os.path.dirname(base)))
    if not dropped:
        print("export: %s/docs/CITATIONS.md already describes the export "
              "tree (%d entr(y/ies))" % (rel, len(heads)))
        return False
    with open(inv, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    print("export: pruned %d entr(y/ies) from %s/docs/CITATIONS.md that no "
          "exported document cites, %d kept (first: %s)"
          % (len(dropped), rel, len(heads) - len(dropped), dropped[0]))
    return True


def rewrite_lint_figure(base, module):
    """Recompute the two numbers this product's SKILL.md prints about its
    own silent-failure lint run, over the EXPORT tree, and return whether
    the sentence changed.

    Same defect as the inventory above, in a single sentence: the counts
    are generated over the hub, the export ships a subset, and the
    product's own eval recomputes them from a live run, so a figure copied
    across the boundary is a number the tree it ships in contradicts. The
    run is the product's own silent_failure_lints, pointed at the export
    tree through the SBE_LINT_ROOT it already reads."""
    skill = os.path.join(base, "SKILL.md")
    if not os.path.isfile(skill):
        return False
    lint = getattr(module, "silent_failure_lints", None)
    if lint is None:
        return False
    try:
        with open(skill, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print("export: %s could not be read (%s), so its lint figures stay "
              "as they were" % (skill, type(e).__name__))
        return False
    match = LINT_FIGURE_RE.search(text)
    if not match:
        return False  # this product's SKILL.md prints no such figure
    saved = os.environ.get("SBE_LINT_ROOT")
    os.environ["SBE_LINT_ROOT"] = base
    # The lint reads its root from sys.argv TOO, and refuses a run that names
    # two: with this process's own arguments left in place, an exporter
    # invoked with any directory argument handed the lint a second root and
    # got a FAIL that had nothing to do with the tree being exported. The
    # root is passed through the environment variable the lint documents,
    # and argv is emptied for exactly the length of the call.
    saved_argv = sys.argv
    sys.argv = sys.argv[:1]
    try:
        verdict, evidence = lint()
    except Exception as e:  # noqa: BLE001 - named below, never swallowed
        print("export: the lint over %s raised %s (%s), so SKILL.md's "
              "figures stay as they were" % (base, type(e).__name__, e))
        return False
    finally:
        sys.argv = saved_argv
        if saved is None:
            os.environ.pop("SBE_LINT_ROOT", None)
        else:
            os.environ["SBE_LINT_ROOT"] = saved
    if verdict != "PASS":
        print("export: the lint over %s reported %s, not PASS, so SKILL.md's "
              "figures stay as they were: %s" % (base, verdict, evidence[:120]))
        return False
    waived = re.search(r"(\d+) suppressed", evidence)
    clean = re.search(r"(\d+) file\(s\) holding no match at all", evidence)
    if not waived or not clean:
        print("export: the lint over %s printed no suppressed or clean-file "
              "count, so SKILL.md's figures stay as they were: %s"
              % (base, evidence[:120]))
        return False
    fresh = ("%s waived hits and %s files that were scanned and genuinely "
             "found clean" % (waived.group(1), clean.group(1)))
    rel = os.path.relpath(base, os.path.dirname(os.path.dirname(base)))
    if fresh == match.group(0):
        print("export: %s/SKILL.md already states the export tree's lint run "
              "(%s)" % (rel, fresh))
        return False
    with open(skill, "w", encoding="utf-8") as fh:
        fh.write(text[:match.start()] + fresh + text[match.end():])
    print("export: %s/SKILL.md now states the export tree's own lint run "
          "(%s, was %s)" % (rel, fresh, match.group(0)))
    return True


def regenerate_published_figures(dest):
    """Every published figure a product computes over its own tree, recomputed
    over the EXPORT tree, and the set of product directories that changed.

    Row E113: docs/CITATIONS.md shipped 137 inventory entries and SKILL.md
    quoted 162 lint-clean files, both computed over the whole hub, into an
    export carrying a subset. A number is a claim about a population, so a
    figure that crosses the export boundary unchanged describes a tree the
    reader does not have: 87 of those entries were cited only by documents
    the export withholds, which failed this product's own citation-inventory
    check at gate severity in every public clone. The hub's own copies stay
    computed over the hub; only the exported copies move.

    Best effort, exactly like regenerate_product_manifests: a product whose
    figures cannot be recomputed is named, loudly, and ships as it was."""
    changed = set()
    products = os.path.join(dest, "products")
    if not os.path.isdir(products):
        return changed
    for name in sorted(os.listdir(products)):
        base = os.path.join(products, name)
        scorer = os.path.join(base, PRODUCT_SCORER_REL)
        if not os.path.isfile(scorer):
            continue
        module = _load_product_module(scorer, "export_figures_%s" % name)
        if module is None:
            continue
        touched = prune_citation_inventory(base, module)
        if rewrite_lint_figure(base, module):
            touched = True
        if touched:
            changed.add(base)
    return changed


def build_orphan_commit(export_dir, allowlist, root=ROOT):
    """The CANDIDATE EXPORT TREE the gates check: one commit, no parent,
    no inherited history, so every gate scores exactly what THIS export
    would newly send. Returns (copied_paths, committed_bool)."""
    _run(["git", "init", "-q"], export_dir)
    _run(["git", "config", "user.name", AUTHOR_NAME], export_dir)
    _run(["git", "config", "user.email", AUTHOR_EMAIL], export_dir)
    copied = build_export_tree(export_dir, allowlist, root)
    # -f: the allowlist and denylist (build_export_tree above) are the only
    # filters this exporter honors. A copied .gitignore (itself just another
    # allowlisted file) must never silently drop a file the allowlist walk
    # already decided to carry, the way it dropped two tracked CSV fixtures
    # force-added past the hub's own root .gitignore's `*.csv` line.
    _run(["git", "add", "-A", "-f"], export_dir)
    status = _run(["git", "status", "--porcelain"], export_dir)
    committed = bool((status.stdout or "").strip())
    if committed:
        author = "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL)
        _run(["git", "commit", "-q", "--author", author, "-m",
              COMMIT_MESSAGE], export_dir)
    return copied, committed


#: ONE list file (E37, 2026-09-03): the one the estate's law names, the same
#: file bm_private_scan.py and the assurance product's history test read.
#: The earlier ~/.claude/private-terms.txt default was a second copy that
#: happened to hold the same terms with nothing keeping it so. Read at call
#: time inside run_gates, never bound as a default argument.
DEFAULT_TERMS_FILE = os.path.expanduser("~/.brothersbe-private-names")


def run_gate(cmd, cwd, name, env=None):
    """One gate command against `cwd`. Returns (ok, verdict_line). ANY
    nonzero exit refuses, FAIL and NO-DATA alike: a check that could not
    run has not certified the tree, never a pass by default."""
    proc = _run(cmd, cwd, env=env)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    verdict = text.splitlines()[-1] if text else "(no output)"
    return proc.returncode == 0, "%s: exit %s, %s" % (
        name, proc.returncode, verdict)


def build_identity_check_dir(identity_dir, allowlist, remote, branch, root=ROOT):
    """A SEPARATE throwaway repo, just for identity_guard.py. Its own
    outgoing-range logic needs a real refs/remotes/origin/HEAD to compare
    against, which the orphan gating tree in build_orphan_commit()
    deliberately never sets (see the module docstring, ORPHAN ON PURPOSE:
    fetching origin there would put the real public repository's remote-
    tracking ref inside cleanse.sh's `git log --all` scope too, reviving
    the same "every export refused forever by legacy history" failure).
    Kept in its own repo, this fetch cannot leak into cleanse's scope.

    Best effort: if the fetch fails (most likely no network), identity_guard
    legitimately reports NO-DATA here, which this exporter's caller already
    treats as a non-pass. That is honest, not a bug: an identity claim this
    exporter could not check against the real remote has not been shown
    clean.

    The one exception is a remote that answers `git ls-remote --heads` with
    no branch at all: the fresh repository of the 2026-09-03 clean-
    extraction decision, where there is nothing to fetch and nothing to
    compare against, so "everything is outgoing" is the honest range. That
    remote gets an EMPTY root commit seeded, by plumbing only (index and
    working tree untouched, exactly as after a real fetch), as
    refs/remotes/origin/<branch> with origin/HEAD pointing at it: the
    precise shape identity_guard.default_remote_branch reads, so the guard
    ranges origin/<branch>..HEAD over exactly the one candidate commit and
    prints its real verdict instead of NO-DATA. A populated remote never
    enters this path, whether or not `branch` exists on it."""
    _run(["git", "init", "-q"], identity_dir)
    _run(["git", "config", "user.name", AUTHOR_NAME], identity_dir)
    _run(["git", "config", "user.email", AUTHOR_EMAIL], identity_dir)
    if remote:
        _run(["git", "remote", "add", "origin", remote], identity_dir)
        fetch = _run(["git", "fetch", "-q", "origin", branch], identity_dir)
        ranged = fetch.returncode == 0
        if not ranged:
            heads = _run(["git", "ls-remote", "--heads", remote], identity_dir)
            if heads.returncode == 0 and not (heads.stdout or "").strip():
                tree = _run(["git", "write-tree"], identity_dir)
                seed = _run(["git", "commit-tree",
                             (tree.stdout or "").strip(), "-m",
                             "empty remote: nothing to compare against"],
                            identity_dir)
                ref = _run(["git", "update-ref",
                            "refs/remotes/origin/%s" % branch,
                            (seed.stdout or "").strip()], identity_dir)
                ranged = (tree.returncode == 0 and seed.returncode == 0
                          and ref.returncode == 0)
                if ranged:
                    print("identity check: %s has no branch; the whole "
                          "candidate is the outgoing range" % remote)
        if ranged:
            _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD",
                  "refs/remotes/origin/%s" % branch], identity_dir)
    build_export_tree(identity_dir, allowlist, root)
    # -f: same reasoning as build_orphan_commit above; the allowlist and
    # denylist are the only filters, never a copied .gitignore.
    _run(["git", "add", "-A", "-f"], identity_dir)
    status = _run(["git", "status", "--porcelain"], identity_dir)
    if (status.stdout or "").strip():
        author = "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL)
        _run(["git", "commit", "-q", "--author", author, "-m",
              COMMIT_MESSAGE], identity_dir)


def build_baseline_dir(baseline_dir, remote, branch):
    """The PUBLIC BASELINE TREE, checked out from `remote`'s current
    `branch` tip: what check_secrets diffs the candidate export against,
    so its scan covers only the lines THIS export would newly add, never
    the long-standing fixture values the public tree already carries
    (credential-detection tests, a changelog reproducing a documented
    example, docs that teach the scanner). A separate throwaway repo from
    both export_dir and identity_dir, for the same reason identity_dir is
    separate from export_dir (see build_identity_check_dir): fetching a
    real remote history into the orphan gating tree would put it inside
    cleanse.sh's `git log --all` scope.

    Returns True when the baseline tree was actually checked out onto
    disk; False when there is nothing to compare against (no remote, or
    the remote has no `branch` at all -- the bootstrap case of the
    2026-09-03 clean-extraction decision, where the whole candidate really
    is outgoing). check_secrets treats False the same as no baseline_dir
    at all: every candidate file is scanned whole, exactly as before this
    parameter existed."""
    if not remote:
        return False
    _run(["git", "init", "-q"], baseline_dir)
    _run(["git", "remote", "add", "origin", remote], baseline_dir)
    fetch = _run(["git", "fetch", "-q", "origin", branch], baseline_dir)
    if fetch.returncode != 0:
        return False
    checkout = _run(["git", "checkout", "-q", "-b", branch,
                      "origin/%s" % branch], baseline_dir)
    return checkout.returncode == 0


def _read_baseline_text(baseline_dir, rel):
    """`baseline_dir`/`rel` as UTF-8 text, or None when there is no
    baseline at all, the file does not exist there (a brand new file), or
    it cannot be decoded as UTF-8 (a binary or unreadable baseline is
    treated exactly like "no baseline": the safe fallback for a secret
    scan is to scan MORE, never less)."""
    if not baseline_dir:
        return None
    baseline_path = os.path.join(baseline_dir, rel)
    if not os.path.isfile(baseline_path):
        return None
    try:
        with open(baseline_path, "rb") as fh:
            return fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _added_lines(candidate_text, baseline_text):
    """The lines of `candidate_text` that are NOT present in
    `baseline_text` (a line-level diff, difflib, standard library, no new
    dependency): only inserted or replaced lines count as added, so a
    line the export merely carried forward unchanged never re-enters the
    scan just because it sits at a different line number. Returns
    `candidate_text` WHOLE when `baseline_text` is None (no baseline at
    all, or a brand new file: nothing to diff against)."""
    if baseline_text is None:
        return candidate_text
    candidate_lines = candidate_text.splitlines()
    baseline_lines = baseline_text.splitlines()
    matcher = difflib.SequenceMatcher(a=baseline_lines, b=candidate_lines,
                                       autojunk=False)
    added = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(candidate_lines[j1:j2])
    return "\n".join(added)


def _first_new_secret(scan_text, shapes, baseline_text):
    """The first SECRET_SHAPES pattern in `shapes` that matches
    `scan_text` with a VALUE not already sitting, verbatim, anywhere in
    `baseline_text`. Line-level diffing (_added_lines, above) is coarse
    by design: a line changed for ONE reason (an unrelated value on it
    was edited) still carries its OTHER values whole, so the changed
    line's full text counts as "added" even though most of it is not new.
    Measured 2026-09-04: products/brothermode/tools/test_bm.py has both a
    known-public AWS example key and an unrelated sk-live-shaped test
    fixture on the SAME source line; the AKIA value being deliberately
    moved onto the public example (a prior commit on this branch) made
    the whole line read as changed, and the untouched sk- fixture rode
    along into the added text. That fixture was already public, verbatim,
    in the baseline; refusing on it would be exactly the false refusal
    this whole feature exists to stop. Checking each MATCHED VALUE against
    the baseline (never the surrounding text) keeps this narrow: a value
    that never appeared in the baseline still refuses, whatever else
    changed on its line. With no baseline_text (no baseline at all, or a
    brand new file) every match counts, matching the pre-baseline
    behaviour: there is nothing yet to compare a value against. Returns
    the matching pattern, or None."""
    for pattern in shapes:
        for m in pattern.finditer(scan_text):
            if baseline_text is not None and m.group(0) in baseline_text:
                continue
            return pattern
    return None


def check_secrets(export_dir, baseline_dir=None):
    """A fourth gate beside cleanse.sh, identity_guard.py and
    private_terms_scan.py. The security gap this closes: push_appended
    pushes through a plain temp `git init` (see that function) with no
    pre-push hook installed there, so scripts/pre_push_gate.py's own
    SECRET_SHAPES check never runs anywhere on this route; a key of the
    shapes pre_push_gate.py already knows (ghp_, sk-, AKIA, a PEM private
    key header) could leave through the export and no gate here would ever
    look. This runs that SAME check, over the SAME shapes
    (pre_push_gate.SECRET_SHAPES, imported rather than duplicated so the
    two never drift apart), against every file the CANDIDATE EXPORT TREE
    actually carries.

    `baseline_dir`, when given (build_baseline_dir's checkout of the public
    remote's current tip; None at bootstrap, when there is nothing to
    compare against), narrows each file's scan to the lines ADDED since
    that baseline (_added_lines, above) -- the same "outgoing range, not
    the whole tree" reasoning pre_push_gate.py already applies to the
    hub's own push, so the twenty-odd long-standing secret-shaped fixture
    values already public in this tree (credential-detection tests, a
    changelog reproducing a documented example, docs that teach the
    scanner) never refuse an export that merely carries them forward
    unchanged; a value newly added anywhere, in an old file or a brand new
    one, still refuses. A line changed for an unrelated reason still counts
    as added whole (line-level diffing is coarse by design), so
    _first_new_secret (below) checks each MATCHED VALUE against the
    baseline text too: a value already sitting, verbatim, anywhere in the
    baseline file is not newly disclosed and does not refuse merely for
    sharing a line with something that did change. With no baseline_dir
    the whole file is scanned, the original behaviour, and the value
    allowlist below still applies: the shape search runs AFTER
    strip_public_examples strips the one documented AWS example value, so
    that value never refuses on its own.

    Refuses on the FIRST file that matches, the file named and the matched
    VALUE never printed, same as pre_push_gate.py's own reasoning for
    silence ("printing it puts it in a terminal and a transcript"). A
    binary file that fails to decode as UTF-8 is skipped: a secret shape is
    a text pattern, and False positives from decoding binary noise as text
    would refuse exports that carry nothing wrong. NO-DATA (a refusal, per
    this module's own law that a check which could not run has not
    certified anything) when pre_push_gate.py cannot even be imported.
    Returns (ok, [line])."""
    try:
        import pre_push_gate
    except Exception as exc:  # noqa: BLE001
        return False, ["secrets: NO-DATA, could not import "
                       "scripts/pre_push_gate.py for its secret shapes "
                       "(%s); the export tree was never scanned for "
                       "secrets" % exc]
    count = 0
    for base, dirs, files in os.walk(export_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            path = os.path.join(base, name)
            count += 1
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                # An unreadable file was never scanned for a secret shape, so
                # a silent skip here would let that file leave through the
                # export unchecked; refuse instead and name it.
                rel = os.path.relpath(path, export_dir)
                return False, ["secrets: NO-DATA, could not read %s (%s); "
                               "the export tree was not fully scanned for "
                               "secrets" % (rel, exc)]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # sbe: allow-silent a secret shape is a text pattern; a file that fails UTF-8 decoding is binary noise, so the observable consequence is a scan this gate cannot perform on non-text bytes, never a text secret let through unscanned
            rel = os.path.relpath(path, export_dir)
            baseline_text = _read_baseline_text(baseline_dir, rel)
            scan_text = strip_public_examples(_added_lines(text, baseline_text))
            if _first_new_secret(scan_text, pre_push_gate.SECRET_SHAPES,
                                  baseline_text) is not None:
                return False, ["REFUSED: a secret-shaped value was found "
                               "in %s; the value is never printed" % rel]
    return True, ["secrets: 0 hit(s) over %d file(s)" % count]


def run_gates(export_dir, identity_dir, baseline_dir=None):
    """cleanse.sh and private_terms_scan.py against the CANDIDATE EXPORT
    TREE (the orphan commit in `export_dir`); identity_guard.py against its
    OWN separate tree (`identity_dir`, see build_identity_check_dir) that
    carries a real origin/HEAD without exposing it to cleanse's `--all`
    history scan; check_secrets (above) against the same candidate tree as
    cleanse.sh, the fourth gate that catches what pre_push_gate.py's own
    SECRET_SHAPES check would have caught had this route run through a
    pre-push hook, which it does not. `baseline_dir` (build_baseline_dir's
    checkout of the public remote's current tip, or None) is passed
    through to check_secrets unchanged, so its scan covers only the lines
    THIS export would newly add. cleanse.sh resolves its own root from
    argv[0]'s directory, so it runs from its COPY inside export_dir (present
    whenever scripts/ is allowlisted, which it is by default); the other two
    subprocess checks take a `cwd` argument instead and run from the hub's
    own copy. Every subprocess gate receives the SAME term list through
    BROTHER_PRIVATE_TERMS in its environment (cleanse.sh and
    identity_guard.py read that variable, private_terms_scan.py also gets
    it as --terms), so the three can never disagree about which file they
    checked. Returns (all_ok, [verdict lines])."""
    terms_file = (os.environ.get("BROTHER_PRIVATE_TERMS")
                  or DEFAULT_TERMS_FILE)
    gate_env = dict(os.environ)
    gate_env["BROTHER_PRIVATE_TERMS"] = terms_file
    export_cleanse = os.path.join(export_dir, "scripts", "cleanse.sh")
    checks = []
    if os.path.isfile(export_cleanse):
        checks.append(("cleanse", ["bash", export_cleanse], export_dir))
    else:
        checks.append(("cleanse", [
            "bash", "-c",
            "echo 'NO-DATA: scripts/cleanse.sh is not in the candidate "
            "export tree (scripts/ is not allowlisted), so cleanse could "
            "not run against it'; exit 2"], export_dir))
    checks.append(("identity_guard",
                    [sys.executable, os.path.join(ROOT, "scripts",
                                                   "identity_guard.py")],
                    identity_dir))
    checks.append(("private_terms_scan",
                    [sys.executable, os.path.join(
                        ROOT, "scripts", "private_terms_scan.py"),
                     "--terms", terms_file, "--range", "HEAD"],
                    export_dir))

    all_ok = True
    lines = []
    for name, cmd, cwd in checks:
        ok, verdict = run_gate(cmd, cwd, name, env=gate_env)
        lines.append(verdict)
        all_ok = all_ok and ok

    secrets_ok, secrets_lines = check_secrets(export_dir, baseline_dir)
    lines.extend(secrets_lines)
    all_ok = all_ok and secrets_ok

    return all_ok, lines


def release_branch_name(tag, export_rev):
    """The branch this export is pushed to before its pull request.
    `release/<version>` for a tagged release, so the branch reads as the
    release it carries; `export/<12 characters of the export commit>` for
    an ordinary content export, which names the exact commit rather than
    inventing a version nobody cut."""
    if tag:
        return RELEASE_BRANCH_PREFIX + tag.lstrip("v")
    return EXPORT_BRANCH_PREFIX + (export_rev or "unknown")[:12]


def push_appended(allowlist, remote, branch, root=ROOT, tag=None,
                   bootstrap=False, run=None, require_signed=False):
    """The real push path, kept separate from gating (see module docstring:
    ORPHAN ON PURPOSE). Fetches `remote`'s current tip, rebuilds the same
    allowlisted content as a new commit ON TOP of it (an honest append),
    then opens and merges a PULL REQUEST for it rather than pushing
    `branch` directly: since the ruleset of row E64 the public repository
    requires a pull request on main, refuses non fast-forward and deletion,
    and grants no bypass, so a direct push is refused with GH013 and the
    only honest route is the one a human contributor takes. Nothing is
    forced, at any step.

    `bootstrap=True` is the one exception: when the remote has no branch at
    all there is no protected branch to open a pull request against, so
    this export becomes the repository's first commit directly; a remote
    with any branch, `branch` itself included, still refuses bootstrap and
    takes the pull request route like everything else.

    With `tag`, tag_time_checks runs over the built export tree before the
    commit, and any finding refuses the whole push: nothing pushed, nothing
    opened, nothing merged, nothing tagged. The tag is cut from the MERGED
    tip of `branch`, fetched back after the merge, never from the local
    commit: what a merge produces on the far side is what a reader clones.

    `run` is the seam every external command goes through (git push, gh pr
    create, gh pr merge, git tag push): it defaults to this module's own
    _run and takes (cmd, cwd, env=None, timeout=...) returning an object
    with returncode, stdout and stderr, so a test can drive the whole
    release route against a local bare repository and a stand-in gh
    without a network. Returns (exit_code, [lines]).

    `require_signed`, with `tag` only: refuse rather than create an
    unsigned tag when no signing key is configured (S5, roadmap; founder
    gated, see signing_configured above). Without it an unsigned tag is
    still created, and the returned lines say so."""
    run = run or _run
    lines = []
    with tempfile.TemporaryDirectory(prefix="brother-export-push-") as d:
        run(["git", "init", "-q"], d)
        run(["git", "config", "user.name", AUTHOR_NAME], d)
        run(["git", "config", "user.email", AUTHOR_EMAIL], d)
        run(["git", "remote", "add", "origin", remote], d)
        if bootstrap:
            # The remote's whole branch list decides, not whether `branch`
            # itself could be fetched: a populated repository refuses
            # --bootstrap whatever its branches are called.
            ls_remote = run(["git", "ls-remote", "--heads", remote], d)
            if ls_remote.returncode != 0:
                lines.append("REFUSED: could not check %s for existing "
                              "branches (%s); this exporter never starts "
                              "unrelated history on the public repository"
                              % (remote, (ls_remote.stderr or "").strip()))
                return EXIT_REFUSED, lines
            existing = [line.split("\t", 1)[1].replace("refs/heads/", "", 1)
                        for line in (ls_remote.stdout or "").splitlines()
                        if "\t" in line]
            if existing:
                lines.append("REFUSED: --bootstrap is only for a "
                              "repository with no branch at all; %s "
                              "already has: %s"
                              % (remote, ", ".join(existing)))
                return EXIT_REFUSED, lines
            orphan = run(["git", "checkout", "-q", "--orphan", branch], d)
            if orphan.returncode != 0:
                lines.append("REFUSED: could not create the orphan "
                              "branch %s (%s)"
                              % (branch, (orphan.stderr or "").strip()))
                return EXIT_REFUSED, lines
            lines.append("BOOTSTRAP: %s has no branch; this export "
                          "becomes the first commit of %s"
                          % (remote, branch))
        else:
            fetch = run(["git", "fetch", "-q", "origin", branch], d)
            if fetch.returncode != 0:
                lines.append("REFUSED: could not fetch %s from %s (%s); "
                              "an append needs the real current tip, and "
                              "this exporter never starts unrelated "
                              "history on the public repository"
                              % (branch, remote,
                                 (fetch.stderr or "").strip()))
                return EXIT_REFUSED, lines
            checkout = run(["git", "checkout", "-q", "-b", branch,
                              "origin/%s" % branch], d)
            if checkout.returncode != 0:
                lines.append("REFUSED: could not check out the fetched "
                              "tip of %s" % branch)
                return EXIT_REFUSED, lines

        if tag:
            # E6.1a, release time: stamp the hub revision this release is
            # cut from into its own release record, in the hub's own
            # working tree, BEFORE the copy below carries it into the
            # export commit. One write lands it in both places a reader
            # would look: the hub's docs/releases file, and the tag that
            # ships it.
            version = tag.lstrip("v")
            source_rev = hub_head_rev(root)
            notes_path = os.path.join(root, "docs", "releases",
                                       "%s.md" % version)
            if source_rev:
                if stamp_source_revision(notes_path, version, source_rev):
                    lines.append("stamped %s with source revision %s"
                                 % (notes_path, source_rev))
            else:
                lines.append("NO-DATA: could not read the hub's own HEAD "
                              "revision; %s not stamped" % notes_path)

        clear_working_tree(d)
        build_export_tree(d, allowlist, root)
        # -f: the allowlist and denylist are the only filters (see the
        # module docstring, THE ALLOWLIST AND DENYLIST ARE THE ONLY
        # FILTERS); staged BEFORE tag_time_checks below so that check reads
        # the exact bytes this add just staged, never the working copy.
        run(["git", "add", "-A", "-f"], d)
        if tag:
            with tempfile.TemporaryDirectory(
                    prefix="brother-export-staged-") as staged:
                # tag_time_checks must prove the COMMITTED bytes clear each
                # product's own verifier, not merely whatever sits on disk
                # in `d`: checkout-index reads the just-staged INDEX, so the
                # manifest this check proves against is the manifest that
                # will actually ship, not a working-tree proxy for it.
                run(["git", "checkout-index", "-a", "-f",
                      "--prefix=%s/" % staged], d)
                checks_ok, check_lines = tag_time_checks(staged, version)
            lines.extend(check_lines)
            if not checks_ok:
                lines.append("REFUSED: the export tree did not clear the "
                              "tag-time checks above; nothing was pushed "
                              "and nothing was tagged")
                return EXIT_REFUSED, lines
        status = run(["git", "status", "--porcelain"], d)
        if not (status.stdout or "").strip():
            lines.append("nothing to push: the export tree already "
                          "matches %s's current tip" % branch)
            return EXIT_OK, lines
        author = "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL)
        run(["git", "commit", "-q", "--author", author, "-m",
              COMMIT_MESSAGE], d)

        env = dict(os.environ)
        env[edition_guard.EXPORT_ENV] = edition_guard.EXPORT_MARK
        lines.append("%s=%s set for this push, the exporter's own marked "
                      "invocation" % (edition_guard.EXPORT_ENV,
                                       edition_guard.EXPORT_MARK))
        head = run(["git", "rev-parse", "HEAD"], d)
        export_rev = (head.stdout or "").strip()
        # bootstrap writes the branch itself, because an empty repository
        # has no protected branch to open a pull request against; every
        # other export writes its own branch and asks for a merge.
        target = branch if bootstrap else release_branch_name(tag, export_rev)
        push = run(["git", "push", remote, "HEAD:refs/heads/%s" % target],
                    d, env=env)
        if (push.stdout or "").strip():
            lines.append((push.stdout or "").strip())
        if (push.stderr or "").strip():
            lines.append((push.stderr or "").strip())
        if push.returncode != 0:
            lines.append("REFUSED: the push itself was rejected by the "
                          "remote (likely not a fast-forward, or a "
                          "connectivity problem). This exporter never "
                          "--force pushes.")
            return EXIT_REFUSED, lines
        lines.append("PUSHED: one commit appended to %s %s" % (remote, target))

        if not bootstrap:
            # THE ONLY ROUTE TO MAIN (row E67, ruleset of row E64): the
            # public repository requires a pull request on main and grants
            # no bypass, so a direct push is refused with GH013. gh opens
            # the request from the branch just pushed and merges it; both
            # run from `d`, whose origin is the remote, so gh resolves the
            # repository the same way it does for a person standing in a
            # clone. A failure at either step refuses: the branch stays on
            # the remote, unmerged and untagged, for a human to look at.
            title = ("export: Brother %s" % tag.lstrip("v") if tag
                     else COMMIT_MESSAGE)
            body = ("Opened by scripts/export_public.py, the single route "
                    "from the private hub to this repository. The commit "
                    "on %s is the allowlisted export tree, already cleared "
                    "by every gate the exporter runs." % target)
            pr = run([GH_BIN, "pr", "create", "--base", branch, "--head",
                      target, "--title", title, "--body", body], d, env=env)
            pr_out = ((pr.stdout or "") + (pr.stderr or "")).strip()
            if pr.returncode != 0:
                lines.append("REFUSED: could not open a pull request for %s "
                              "(gh exit %s, %s); the branch is pushed and "
                              "nothing was merged or tagged"
                              % (target, pr.returncode,
                                 pr_out.splitlines()[-1] if pr_out
                                 else "(no output)"))
                return EXIT_REFUSED, lines
            urls = [l.strip() for l in pr_out.splitlines()
                    if l.strip().startswith("http")]
            pr_ref = urls[-1] if urls else target
            lines.append("PULL-REQUEST: %s" % pr_ref)
            merge = run([GH_BIN, "pr", "merge", pr_ref, "--merge",
                          "--delete-branch"], d, env=env)
            merge_out = ((merge.stdout or "") + (merge.stderr or "")).strip()
            if merge.returncode != 0:
                lines.append("REFUSED: the pull request %s was opened but "
                              "not merged (gh exit %s, %s); nothing was "
                              "tagged"
                              % (pr_ref, merge.returncode,
                                 merge_out.splitlines()[-1] if merge_out
                                 else "(no output)"))
                return EXIT_REFUSED, lines
            lines.append("MERGED: %s into %s" % (pr_ref, branch))

        if tag:
            # Release identity (productization directive A4, founder-ordered
            # 2026-08-31): a release needs a tag on the public repository,
            # the edition guard correctly refuses every direct session push
            # there, so the ONE allowed invocation grows the ONE extra ref a
            # release needs. Annotated, never forced: an existing tag of the
            # same name refuses rather than moves. It points at what the
            # MERGE produced on the far side, fetched back here, never at
            # the local commit: a merge commit is what a reader clones, and
            # a tag on anything else names a tree nobody can check out.
            if bootstrap:
                target_rev = branch
            else:
                fetch_merged = run(["git", "fetch", "-q", "origin", branch], d)
                if fetch_merged.returncode != 0:
                    lines.append("REFUSED: the pull request merged but the "
                                  "merged tip of %s could not be fetched "
                                  "back to tag it (%s); no tag was pushed"
                                  % (branch,
                                     (fetch_merged.stderr or "").strip()))
                    return EXIT_REFUSED, lines
                target_rev = "FETCH_HEAD"
            signed, key_id = signing_configured(d, run)
            if signed:
                lines.append("tag signing: git tag -s using key %s "
                              "(user.signingkey configured)" % key_id)
                sign_flag = "-s"
            else:
                lines.append("tag signing: NO-DATA: no signing key "
                              "configured (S5, founder)")
                if require_signed:
                    lines.append("REFUSED: --require-signed set and no "
                                  "signing key is configured; refusing to "
                                  "create an unsigned tag")
                    return EXIT_REFUSED, lines
                sign_flag = "-a"
            tagged = run(["git", "tag", sign_flag, tag, "-m",
                           "Brother %s" % tag.lstrip("v"), target_rev], d)
            if tagged.returncode != 0:
                lines.append("REFUSED: could not create tag %s locally (%s)"
                             % (tag, (tagged.stderr or "").strip()))
                return EXIT_REFUSED, lines
            tag_push = run(["git", "push", remote, "refs/tags/%s" % tag],
                            d, env=env)
            if (tag_push.stderr or "").strip():
                lines.append((tag_push.stderr or "").strip())
            if tag_push.returncode != 0:
                lines.append("REFUSED: the tag push was rejected; an "
                             "existing %s is never moved, this exporter "
                             "never --force pushes" % tag)
                return EXIT_REFUSED, lines
            lines.append("TAGGED: %s points at %s on %s"
                         % (tag, "the first commit" if bootstrap
                            else "the merged tip", branch))
        return EXIT_OK, lines


def clear_working_tree(export_dir):
    """Remove everything in `export_dir` except .git, so a file the
    allowlist dropped (or the fetched tip carried) does not survive into
    the new commit as a stale leftover."""
    for entry in os.listdir(export_dir):
        if entry == ".git":
            continue
        path = os.path.join(export_dir, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def _ensure_git_tree(export_dir):
    """Make the export tree a git repository holding exactly its own bytes,
    when it is not one already. tag_time_checks runs against a `git
    checkout-index` copy (push_appended stages first on purpose, so the
    checks read the bytes that will really ship), and that copy carries no
    .git. The three checks below answer "does this work in a FRESH CLONE",
    and part of the README's own proof reads the tree's git revision
    (scripts/test_brother_run.py asserts a receipt's harness_revision is
    the repository's HEAD, and reports NO-DATA on a plain directory), so a
    directory with no history would refuse every tag for a reason no clone
    will ever have. Every product's own verify-install.sh runs no git at
    all and skips ./.git/* in its walk, so this repository is invisible to
    the checks that already ran above it. Returns (ok, lines): a tree that
    cannot be initialised is NO-DATA, never a silent pass."""
    if os.path.isdir(os.path.join(export_dir, ".git")):
        return True, []
    steps = [
        ["git", "init", "-q"],
        ["git", "config", "user.name", AUTHOR_NAME],
        ["git", "config", "user.email", AUTHOR_EMAIL],
        ["git", "add", "-A", "-f"],
        ["git", "commit", "-q", "--author",
         "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL), "-m", COMMIT_MESSAGE],
    ]
    for cmd in steps:
        proc = _run(cmd, export_dir, timeout=300)
        if proc.returncode != 0:
            text = ((proc.stdout or "") + (proc.stderr or "")).strip()
            return False, ["NO-DATA: could not make the export tree a git "
                           "repository for the fresh-clone checks (%s exit "
                           "%s, %s)" % (" ".join(cmd), proc.returncode,
                                        text.splitlines()[-1] if text
                                        else "(no output)")]
    return True, []


def check_readiness_gate(export_dir):
    """The export tree's own readiness gate must read READY before a tag.
    E67, measured in a fresh clone of the public tag v1.0.1: `python3
    scripts/readiness_gate.py` printed "GATE: NOT READY. 1 critical item(s)
    unproven: Restore drill (NO-DATA)" while the same command in the hub
    printed ready, because docs/plan/RESTORE-DRILL-ENTERPRISE-RESULT.json
    was never on the allowlist and the exporter's tag-time checks ran the
    product verifiers but never the gate. READY is exit 0 AND a GATE: line
    that does not say NOT READY. Every other outcome refuses with the
    gate's own line quoted, a missing gate script included: NO-DATA is
    never a pass. Returns (ok, lines)."""
    gate_path = os.path.join(export_dir, READINESS_GATE_REL)
    if not os.path.isfile(gate_path):
        return False, ["NO-DATA: the export tree carries no %s, so its own "
                       "readiness could not be read" % READINESS_GATE_REL]
    proc = _run(["python3", READINESS_GATE_REL], export_dir, timeout=1800)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    verdicts = [l.strip() for l in text.splitlines()
                if l.strip().startswith("GATE:")]
    if not verdicts:
        last = text.splitlines()[-1] if text else "(no output)"
        return False, ["NO-DATA: %s printed no GATE: line on the export "
                       "tree (exit %s, %s)"
                       % (READINESS_GATE_REL, proc.returncode, last)]
    verdict = verdicts[-1]
    if proc.returncode != 0 or "NOT READY" in verdict:
        return False, (["REFUSED: the export tree's own readiness gate does "
                        "not read READY (exit %s): %s"
                        % (proc.returncode, verdict)]
                       + _readiness_failing_items(text))
    return True, ["readiness: %s" % verdict]


def _readiness_failing_items(text):
    """The gate's own item lines under its NOT READY summary, quoted.

    WHY: measured 2026-09-04 on the 1.0.2 cut, the refusal above printed only
    "GATE: NOT READY." and the operator had to rebuild the export tree by
    hand to learn WHICH item was unproven. The gate already names them, one
    "  - Title (VERDICT)" line per item under each "N ... item(s)" heading,
    so the refusal quotes them rather than hiding them behind a re-run.
    Returns [] when the gate printed no such lines (an older gate, or a
    refusal with no items), never a fabricated line."""
    items = []
    after_verdict = False
    for line in text.splitlines():
        if line.strip().startswith("GATE:"):
            after_verdict = "NOT READY" in line
            continue
        if after_verdict and line.strip().startswith("- "):
            items.append("  gate item: %s" % line.strip()[2:].strip())
    return items


def check_required_fast(export_dir):
    """The public repository's own required-fast contract must read fail 0
    on the export tree, not only in the hub. required-fast is becoming a
    mandatory GitHub check on the public repository's release pull
    requests, so a tag whose export tree cannot clear its own required
    check has certified nothing: the very first real pull request would
    fail the check this exporter just tagged past. Runs
    scripts/required_fast.sh in export_dir exactly as a fresh clone would
    (same working directory, no flags), and reads its own summary line
    rather than only its exit code, so the refusal names which check(s)
    failed. NO-DATA lines are named but never treated as a failure, the
    same rule required_fast.sh applies to itself. Returns (ok, [lines])."""
    script = os.path.join(export_dir, "scripts", "required_fast.sh")
    if not os.path.isfile(script):
        return False, ["NO-DATA: the export tree carries no "
                       "scripts/required_fast.sh, so its own required "
                       "check could not be run on it"]
    proc = _run(["sh", script], export_dir, timeout=600)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    summary = re.search(r"^pass\s+(\d+)\s+fail\s+(\d+)\s+no-data\s+(\d+)",
                        text, re.M)
    if not summary:
        last = text.splitlines()[-1] if text else "(no output)"
        return False, ["NO-DATA: scripts/required_fast.sh printed no "
                       "summary line on the export tree (exit %s, %s)"
                       % (proc.returncode, last)]
    fails = int(summary.group(2))
    if fails > 0 or proc.returncode != 0:
        lines = ["REFUSED: scripts/required_fast.sh does not read fail 0 "
                "on the export tree (exit %s): %s"
                % (proc.returncode, summary.group(0))]
        failed = next((l for l in text.splitlines()
                      if l.startswith("FAILED:")), None)
        if failed:
            lines.append(failed)
        return False, lines
    return True, ["required-fast: %s" % summary.group(0)]


def check_markdown_links(export_dir):
    """Every relative `[text](target)` link in every .md file of the export
    tree must resolve to a file the export tree actually carries. E70,
    measured on the public tag v1.0.1: three of README.md's six relative
    links (docs/for-engineers/00-START-HERE.md, docs/for-engineers/
    STARTUP-WEEK.md, docs/for-analysts/00-START-HERE.md) pointed at pages
    the 2026-09-01 narrowing had dropped from the allowlist, and no
    tag-time check looked. http, https and mailto targets are counted and
    skipped (this exporter reaches no network); a bare `#anchor` points
    inside its own page, not at a file; a `path#anchor` is resolved by its
    path half. A target that resolves OUTSIDE the export tree is dead too:
    it would not exist in a clone. Returns (ok, lines): one line per dead
    link naming the file and the target, then the summary line."""
    root_abs = os.path.abspath(export_dir)
    resolved = 0
    external = 0
    dead = []
    for base, dirs, files in os.walk(export_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, export_dir)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError) as exc:
                dead.append("dead link: %s could not be read (%s)"
                            % (rel, exc))
                continue
            for target in MD_LINK_RE.findall(text):
                if target.startswith(("http://", "https://", "mailto:")):
                    external += 1
                    continue
                if target.startswith("#"):
                    continue
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue
                full = os.path.abspath(os.path.join(base, target_path))
                inside = (full == root_abs
                          or full.startswith(root_abs + os.sep))
                if inside and os.path.exists(full):
                    resolved += 1
                else:
                    dead.append("dead link: %s points at %s, which the "
                                "export tree does not carry" % (rel, target))
    lines = list(dead)
    lines.append("links: %d resolved, %d external skipped, %d dead"
                 % (resolved, external, len(dead)))
    return not dead, lines


def readme_prove_commands(text):
    """Every `python3 scripts/test_*.py` the README names, in the order it
    names them, each once. The README calls these its own proof ("Prove the
    rule with...", "Run the check that holds this rule in place"), so they
    are the commands a reader runs first in a fresh clone."""
    seen = []
    for match in README_PROVE_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def check_readme_prove_commands(export_dir):
    """Every command the README names as its own proof must really run in
    the export tree. E70, measured in a fresh clone of v1.0.1: `python3
    scripts/test_battery_verdict.py`, named in the README as the proof that
    a battery cannot hide its red lines, died with FileNotFoundError on
    docs/plan/BATTERY-EXPECTATIONS.json, a file the allowlist never
    carried. Refuses on the FIRST failing command so the output names the
    one that broke rather than a wall of them. An absent README, or a
    README naming no such command, is NO-DATA and refuses too: a public tag
    whose own front page proves nothing has not been checked, and this
    exporter never reads NO-DATA as a pass. Returns (ok, lines)."""
    readme = os.path.join(export_dir, "README.md")
    if not os.path.isfile(readme):
        return False, ["NO-DATA: the export tree carries no README.md, so "
                       "no prove command could be run on it"]
    try:
        with open(readme, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return False, ["NO-DATA: the export tree's README.md could not be "
                       "read (%s)" % exc]
    commands = readme_prove_commands(text)
    if not commands:
        return False, ["NO-DATA: the export tree's README.md names no "
                       "command of the shape python3 scripts/test_*.py, so "
                       "nothing it claims was proven on the export tree"]
    lines = []
    for command in commands:
        proc = _run(command.split(), export_dir, timeout=1800)
        lines.append("prove: %s exit %s" % (command, proc.returncode))
        if proc.returncode != 0:
            text = ((proc.stdout or "") + (proc.stderr or "")).strip()
            lines.append("REFUSED: the README names %s as its own proof and "
                         "it fails on the export tree: %s"
                         % (command, text.splitlines()[-1] if text
                            else "(no output)"))
            return False, lines
    return True, lines


def check_export_manifest(export_dir, version, built_text=None):
    """E110: the export manifest a tag SHIPS must describe the bytes that
    same tag ships, not the tree some earlier commit had.

    Measured on the public tag v1.0.2: it carries a 1198 line
    docs/releases/1.0.2.export-manifest.txt generated at the cut commit,
    and 18 of those files hash differently in the tag itself (for one,
    bundle/runtime/worktree_lane.py). Four commits landed on the cut
    branch after the manifest was written, nothing regenerated it, and
    nothing compared it to the export tree before the tag was pushed. The
    refresh is `python3 scripts/release_note_from_tree.py --write
    --version <version>`, which rewrites the manifest and the note that
    states its digest together; this check is what refuses a tag that
    skipped it.

    The comparison is the exporter's own manifest of the tree in hand
    against the manifest the tree carries, so it names three kinds of
    offender at once: a file whose bytes moved, a file the manifest names
    that is not shipped, and a file that is shipped and the manifest never
    named. Files under reproduce_export.MANIFEST_EXCLUDED_PREFIX are
    outside the manifest by construction on both sides (the note carrying
    the digest cannot hash itself), so they can never be offenders here.
    An absent or unparseable manifest is NO-DATA and refuses: a tag whose
    own contents claim is unreadable has not been checked. Returns
    (ok, lines)."""
    # Lazy on purpose: reproduce_export imports THIS module, so a
    # top level import here would be circular.
    import reproduce_export as RE

    rel = RE.manifest_path_for(version)
    path = os.path.join(export_dir, *rel.split("/"))
    if not os.path.isfile(path):
        return False, ["NO-DATA: the export tree carries no %s, so nothing "
                       "in it describes the bytes this tag would ship"
                       % rel]
    try:
        with open(path, encoding="utf-8") as fh:
            shipped_text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return False, ["NO-DATA: %s in the export tree could not be read "
                       "(%s)" % (rel, exc)]
    shipped = RE.parse_manifest(shipped_text)
    if shipped is None:
        return False, ["NO-DATA: %s is not in the '<sha256>  <path>' shape "
                       "this estate's manifests are written in, so it "
                       "names nothing that can be checked" % rel]
    try:
        # The README proof suites run inside this tree before this check
        # and leave bytecode and a fence store behind (attempt 3 of the
        # 1.0.3 tag read 70 EXTRA files, every one a __pycache__ entry or
        # .brothermode/recurrence.sqlite3); the tag ships the tree as it
        # was STAGED, so the caller hashes it before any proof runs and
        # hands that snapshot in. A caller with no snapshot hashes now.
        built = RE.parse_manifest(built_text if built_text is not None
                                  else RE.manifest_from_dir(export_dir))
    except OSError as exc:
        return False, ["NO-DATA: the export tree could not be hashed to "
                       "compare against %s (%s)" % (rel, exc)]
    if built is None:
        return False, ["NO-DATA: the export tree hashes to no manifest "
                       "entry at all, so there is nothing for %s to "
                       "describe" % rel]
    named = dict((p, sha) for sha, p in shipped)
    actual = dict((p, sha) for sha, p in built)
    offenders = []
    for p in sorted(named):
        if p not in actual:
            offenders.append("MISSING: %s is named in %s but the export "
                             "tree does not ship it" % (p, rel))
        elif actual[p] != named[p]:
            offenders.append("MISMATCH: %s ships as %s, %s names %s"
                             % (p, actual[p], rel, named[p]))
    for p in sorted(actual):
        if p not in named:
            offenders.append("EXTRA: %s is shipped and %s does not name it"
                             % (p, rel))
    if offenders:
        lines = offenders[:3]
        lines.append("REFUSED: %d of %d file(s) this tag would ship "
                     "disagree with %s; regenerate it with python3 "
                     "scripts/release_note_from_tree.py --write --version "
                     "%s and commit that before tagging"
                     % (len(offenders), len(actual), rel, version))
        return False, lines
    # DELIBERATELY NOT CHECKED HERE: whether docs/releases/<version>.md
    # states this manifest's digest. reproduce_export.verify_tree already
    # compares those two from a public clone, which is where that claim is
    # read, and duplicating it here would refuse every export tree that
    # ships a note without a digest sentence for a reason that has nothing
    # to do with the bytes.
    return True, ["manifest: %s describes all %d exported file(s) this tag "
                  "would ship (files under %s are outside it by design)"
                  % (rel, len(actual), RE.MANIFEST_EXCLUDED_PREFIX)]


def tag_time_checks(export_dir, version):
    """What stands between a built export tree and a public TAG (never a
    plain export). (1) docs/releases/<version>.md in the export tree must
    not still carry the placeholder Source revision block that
    stamp_source_revision seeds and replaces: the shipped 0.9.11 note did,
    because the stamp could not fire on that cut and nothing refused. The
    block match (header, blank line, placeholder, blank line) is the one
    scripts/release_notes_stamped.py uses, on purpose: a note that merely
    QUOTES the placeholder in prose is not refused. (2) Every product under
    products/ that ships scripts/verify-install.sh must PASS it against the
    export tree, run from that product's own directory exactly as an
    installer would: at the public tag v0.9.11 each product's
    CHECKSUMS.sha256 was the hub's manifest, not the export's, and the
    verifier printed FAILED there (304 match, 492 missing; 451 match, 980
    missing). regenerate_product_manifests now rebuilds the manifests over
    the export tree; this is what proves it, on the candidate, before the
    tag is pushed. Returns (ok, [lines]), one line per product either
    way."""
    lines = []
    # Hash the staged tree BEFORE any proof suite runs in it, so the
    # manifest check below compares what the tag ships, not the residue
    # the proofs leave (see check_export_manifest).
    try:
        import reproduce_export as RE
        built_text = RE.manifest_from_dir(export_dir)
    except (OSError, ImportError):
        built_text = None
    ok = True
    notes_rel = "docs/releases/%s.md" % version
    notes_path = os.path.join(export_dir, "docs", "releases",
                              "%s.md" % version)
    if os.path.isfile(notes_path):
        try:
            with open(notes_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            lines.append("REFUSED: could not read %s in the export tree "
                          "(%s)" % (notes_rel, exc))
            return False, lines
        placeholder_block = "%s\n\n%s\n\n" % (SOURCE_REVISION_HEADER,
                                               SOURCE_REVISION_PLACEHOLDER)
        if placeholder_block in text:
            lines.append("REFUSED: %s still carries the placeholder source "
                          "revision; a tag must carry the hub revision it "
                          "was cut from" % notes_rel)
            return False, lines
    products_dir = os.path.join(export_dir, "products")
    names = (sorted(os.listdir(products_dir))
             if os.path.isdir(products_dir) else [])
    for name in names:
        product_dir = os.path.join(products_dir, name)
        verifier = os.path.join(product_dir, "scripts", "verify-install.sh")
        if not os.path.isfile(verifier):
            continue
        proc = _run(["bash", verifier], product_dir, timeout=600)
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        last = text.splitlines()[-1] if text else "(no output)"
        if proc.returncode != 0:
            lines.append("REFUSED: products/%s's own install verifier fails "
                          "on the export tree: %s" % (name, last))
            ok = False
            continue
        match = re.search(r"^verify-install: (\d+) file\(s\) match", text,
                          re.M)
        lines.append("verified: products/%s install verifier PASS on the "
                      "export tree, %s"
                      % (name, "%s file(s)" % match.group(1) if match
                         else "count line not printed"))
    # (3) to (5), rows E67 and E70: the export tree must be READY by its
    # own gate, every relative link in it must resolve inside it, and every
    # command the README calls its own proof must really run there. All
    # three are run on a git-initialised copy, which is the state a fresh
    # clone is in, and each refuses rather than reporting and continuing.
    git_ok, git_lines = _ensure_git_tree(export_dir)
    lines.extend(git_lines)
    if not git_ok:
        return False, lines
    for check in (check_readiness_gate, check_markdown_links,
                  check_readme_prove_commands):
        check_ok, check_lines = check(export_dir)
        lines.extend(check_lines)
        if not check_ok:
            # Fail fast on purpose: the prove commands run the export
            # tree's own test suites, minutes of work that prove nothing
            # once an earlier check has already refused the tag.
            return False, lines
    # (6), row E110: the manifest this tag ships must describe this tag's
    # own bytes. LAST on purpose, even though it is the cheapest check
    # here: every refusal above names something specific about the tree,
    # and a tag whose manifest is stale is almost always ALSO stale in one
    # of those ways, so running this first would relabel other people's
    # refusals as a manifest problem. An operator who wants the cheap
    # answer before spending the minutes above runs
    # `python3 scripts/refresh_cut.py --version <version> --check`.
    manifest_ok, manifest_lines = check_export_manifest(export_dir, version,
                                                        built_text=built_text)
    lines.extend(manifest_lines)
    if not manifest_ok:
        return False, lines
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--allowlist", default=None)
    ap.add_argument("--root", default=ROOT,
                     help="the hub root to copy allowlisted paths from "
                          "(default: this script's own hub checkout)")
    ap.add_argument("--remote", default=DEFAULT_REMOTE,
                     help="the public repository URL (default: %s). "
                          "Required for --push." % DEFAULT_REMOTE)
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    ap.add_argument("--push", action="store_true",
                     help="actually push, after the gates clear. Without "
                          "this flag nothing is pushed and no ref is "
                          "written anywhere; the dry run still FETCHES "
                          "the remote's tip, read only, into a throwaway "
                          "directory so identity_guard.py can compare "
                          "against origin/<branch>.")
    ap.add_argument("--dry-run", action="store_true",
                     help="accepted for readability; this is already the "
                          "default whenever --push is absent")
    ap.add_argument("--prove-required-fast", action="store_true",
                     help="after the candidate export tree's other gates "
                          "clear, also run scripts/required_fast.sh inside "
                          "it and refuse unless it reads fail 0. Off by "
                          "default so a plain dry run stays fast "
                          "(required_fast.sh is itself a multi-minute "
                          "battery); a release cut passes this so the "
                          "public repository's own required check cannot "
                          "regress unnoticed. Works with or without --push.")
    ap.add_argument("--tag", default=None,
                     help="with --push only: after the export commit lands, "
                          "create an annotated tag of this name pointing at "
                          "it and push that one ref under the same marked "
                          "invocation. Never forced; an existing tag "
                          "refuses. This is the release identity road "
                          "(directive A4): sessions cannot push the public "
                          "repository, so the single allowed invocation "
                          "carries the single extra ref a release needs. "
                          "Before the push, the export tree must clear "
                          "tag_time_checks: its release note is stamped, "
                          "every product's own verify-install.sh passes "
                          "on it, its own readiness_gate.py reads READY "
                          "there, every relative markdown link resolves, "
                          "and every README prove command runs.")
    ap.add_argument("--bootstrap", action="store_true",
                     help="with --push only: when the remote has no "
                          "branch at all, start it with this export as "
                          "its first commit (the clean-extraction route "
                          "of the 2026-09-03 decision); refused when the "
                          "remote has any branch, and refused without "
                          "--push")
    ap.add_argument("--require-signed", action="store_true",
                     help="with --tag only: refuse the cut rather than "
                          "create an unsigned tag when no signing key is "
                          "configured (git config user.signingkey plus "
                          "gpg.format or a matching gpg secret key). S5, "
                          "roadmap: the key is the founder's, this only "
                          "reads what he has already set up.")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    allowlist = load_allowlist(args.allowlist)
    if allowlist is None:
        print("NO-DATA: no allowlist at %s; refusing to export anything."
              % (args.allowlist or DEFAULT_ALLOWLIST))
        return EXIT_NODATA

    with tempfile.TemporaryDirectory(prefix="brother-export-") as export_dir, \
         tempfile.TemporaryDirectory(prefix="brother-export-identity-") as identity_dir, \
         tempfile.TemporaryDirectory(prefix="brother-export-baseline-") as baseline_tmp:
        copied, committed = build_orphan_commit(export_dir, allowlist,
                                                 args.root)
        print("candidate export tree: %d root path(s) copied (%s)"
              % (len(copied), ", ".join(sorted(copied)) or "none"))
        if not committed:
            print("NO-DATA: the allowlist named no path that exists under "
                  "%s, so there is nothing to gate or export" % args.root)
            return EXIT_NODATA

        build_identity_check_dir(identity_dir, allowlist, args.remote,
                                  args.branch, args.root)
        has_baseline = build_baseline_dir(baseline_tmp, args.remote,
                                           args.branch)
        baseline_dir = baseline_tmp if has_baseline else None
        print("secrets baseline: %s"
              % ("%s %s checked out; the secret scan covers only lines "
                 "this export would newly add"
                 % (args.remote, args.branch) if has_baseline
                 else "none (no remote branch to compare against); every "
                      "candidate file is scanned whole"))
        gates_ok, gate_lines = run_gates(export_dir, identity_dir,
                                          baseline_dir)
        for line in gate_lines:
            print(line)

        if not gates_ok:
            print("REFUSED: at least one gate did not clear on the "
                  "candidate export tree. Nothing was pushed.")
            return EXIT_REFUSED

        print("CLEAR: every gate passed on the candidate export tree "
              "(%d file/path entr%s)"
              % (len(copied), "y" if len(copied) == 1 else "ies"))

        if args.prove_required_fast:
            # required_fast.sh expects a real git tree under it (several of
            # its own checks shell out to git); the orphan candidate above
            # was only ever committed for the secret/identity gates, so
            # give required_fast.sh the same fresh-clone state tag_time_
            # checks gives readiness_gate.py and its siblings.
            git_ok, git_lines = _ensure_git_tree(export_dir)
            for line in git_lines:
                print(line)
            if not git_ok:
                return EXIT_REFUSED
            rf_ok, rf_lines = check_required_fast(export_dir)
            for line in rf_lines:
                print(line)
            if not rf_ok:
                print("REFUSED: the candidate export tree does not clear "
                      "its own required-fast check. Nothing was pushed.")
                return EXIT_REFUSED

    if not args.push:
        if args.tag:
            print("REFUSED: --tag only means something with --push; a "
                  "dry run never creates a ref anywhere")
            return EXIT_REFUSED
        if args.bootstrap:
            print("REFUSED: --bootstrap only means something with --push")
            return EXIT_REFUSED
        if args.require_signed:
            print("REFUSED: --require-signed only means something with "
                  "--tag and --push")
            return EXIT_REFUSED
        print("DRY-RUN: no push performed. Pass --push (with --remote) "
              "to push for real.")
        return EXIT_OK

    if not args.remote:
        print("NO-DATA: --push requires --remote")
        return EXIT_NODATA

    if args.require_signed and not args.tag:
        print("REFUSED: --require-signed only means something with --tag")
        return EXIT_REFUSED

    code, lines = push_appended(allowlist, args.remote, args.branch,
                                args.root, tag=args.tag,
                                bootstrap=args.bootstrap,
                                require_signed=args.require_signed)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
