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
     prints that it did) for the one subprocess call that pushes, and
     pushes with a plain (non-force) push, which the remote itself refuses
     if it would not fast-forward. NEVER --force: an append that is not a
     fast-forward is a design assumption that broke, not a reason to
     overwrite history. The gates are NOT re-run on this second tree: its
     file content is byte-identical to the orphan already gated in step 3,
     only its git parent differs. --bootstrap (with --push only) is the
     one exception to the append rule: when the remote has no branch at
     all, this export becomes its first commit instead of refusing. A
     remote with any branch, of any name, still refuses --bootstrap
     outright, so the flag only ever opens the empty-repository case the
     2026-09-03 clean-extraction decision created, never a route to start
     unrelated history over a populated one. --tag (with --push only)
     adds one annotated tag, and before anything is pushed the export
     tree must clear tag_time_checks: the release note it carries is
     really stamped (no placeholder Source revision block), and every
     product's own scripts/verify-install.sh passes on it, which is what
     proves the regenerated manifests describe the shipped bytes.

Python 3, standard library only. No network beyond git's own fetch/push.
"""
import argparse
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


def build_export_tree(dest, allowlist, root=ROOT):
    """Copy every allowlisted path from `root` into `dest`. A listed path
    that does not exist in `root` is skipped, never invented. Last step:
    regenerate_product_manifests(dest), so any product's own
    CHECKSUMS.sha256 that landed in `dest` describes the files that
    actually landed there, not the full hub tree it was generated over.
    Returns the list of paths actually copied."""
    copied = []
    for rel in allowlist:
        rel = rel.strip("/")
        if not rel or rel in HARD_EXCLUDE or rel.startswith("editions/"):
            continue  # the hard boundary, regardless of what the list says
        src = os.path.join(root, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(dest, rel)
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        copied.append(rel)
    for deny_rel in load_denylist():
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
    return copied


def regenerate_product_manifests(dest):
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


def build_orphan_commit(export_dir, allowlist, root=ROOT):
    """The CANDIDATE EXPORT TREE the gates check: one commit, no parent,
    no inherited history, so every gate scores exactly what THIS export
    would newly send. Returns (copied_paths, committed_bool)."""
    _run(["git", "init", "-q"], export_dir)
    _run(["git", "config", "user.name", AUTHOR_NAME], export_dir)
    _run(["git", "config", "user.email", AUTHOR_EMAIL], export_dir)
    copied = build_export_tree(export_dir, allowlist, root)
    _run(["git", "add", "-A"], export_dir)
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
    _run(["git", "add", "-A"], identity_dir)
    status = _run(["git", "status", "--porcelain"], identity_dir)
    if (status.stdout or "").strip():
        author = "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL)
        _run(["git", "commit", "-q", "--author", author, "-m",
              COMMIT_MESSAGE], identity_dir)


def run_gates(export_dir, identity_dir):
    """cleanse.sh and private_terms_scan.py against the CANDIDATE EXPORT
    TREE (the orphan commit in `export_dir`); identity_guard.py against its
    OWN separate tree (`identity_dir`, see build_identity_check_dir) that
    carries a real origin/HEAD without exposing it to cleanse's `--all`
    history scan. cleanse.sh resolves its own root from argv[0]'s
    directory, so it runs from its COPY inside export_dir (present whenever
    scripts/ is allowlisted, which it is by default); the other two take a
    `cwd` argument instead and run from the hub's own copy. Every gate
    receives the SAME term list through BROTHER_PRIVATE_TERMS in its
    environment (cleanse.sh and identity_guard.py read that variable,
    private_terms_scan.py also gets it as --terms), so the three can never
    disagree about which file they checked. Returns (all_ok, [verdict
    lines])."""
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
    return all_ok, lines


def push_appended(allowlist, remote, branch, root=ROOT, tag=None,
                   bootstrap=False):
    """The real push path, kept separate from gating (see module docstring:
    ORPHAN ON PURPOSE). Fetches `remote`'s current tip, rebuilds the same
    allowlisted content as a new commit ON TOP of it (an honest append),
    and pushes it with no --force. `bootstrap=True` is the one exception:
    when the remote has no branch at all, this export becomes its first
    commit instead of refusing; a remote with any branch, `branch` itself
    included, still refuses. With `tag`, tag_time_checks runs over the
    built export tree before the commit, and any finding refuses the whole
    push: nothing pushed, nothing tagged. Returns (exit_code, [lines])."""
    lines = []
    with tempfile.TemporaryDirectory(prefix="brother-export-push-") as d:
        _run(["git", "init", "-q"], d)
        _run(["git", "config", "user.name", AUTHOR_NAME], d)
        _run(["git", "config", "user.email", AUTHOR_EMAIL], d)
        _run(["git", "remote", "add", "origin", remote], d)
        if bootstrap:
            # The remote's whole branch list decides, not whether `branch`
            # itself could be fetched: a populated repository refuses
            # --bootstrap whatever its branches are called.
            ls_remote = _run(["git", "ls-remote", "--heads", remote], d)
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
            orphan = _run(["git", "checkout", "-q", "--orphan", branch], d)
            if orphan.returncode != 0:
                lines.append("REFUSED: could not create the orphan "
                              "branch %s (%s)"
                              % (branch, (orphan.stderr or "").strip()))
                return EXIT_REFUSED, lines
            lines.append("BOOTSTRAP: %s has no branch; this export "
                          "becomes the first commit of %s"
                          % (remote, branch))
        else:
            fetch = _run(["git", "fetch", "-q", "origin", branch], d)
            if fetch.returncode != 0:
                lines.append("REFUSED: could not fetch %s from %s (%s); "
                              "an append needs the real current tip, and "
                              "this exporter never starts unrelated "
                              "history on the public repository"
                              % (branch, remote,
                                 (fetch.stderr or "").strip()))
                return EXIT_REFUSED, lines
            checkout = _run(["git", "checkout", "-q", "-b", branch,
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
        if tag:
            checks_ok, check_lines = tag_time_checks(d, version)
            lines.extend(check_lines)
            if not checks_ok:
                lines.append("REFUSED: the export tree did not clear the "
                              "tag-time checks above; nothing was pushed "
                              "and nothing was tagged")
                return EXIT_REFUSED, lines
        _run(["git", "add", "-A"], d)
        status = _run(["git", "status", "--porcelain"], d)
        if not (status.stdout or "").strip():
            lines.append("nothing to push: the export tree already "
                          "matches %s's current tip" % branch)
            return EXIT_OK, lines
        author = "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL)
        _run(["git", "commit", "-q", "--author", author, "-m",
              COMMIT_MESSAGE], d)

        env = dict(os.environ)
        env[edition_guard.EXPORT_ENV] = edition_guard.EXPORT_MARK
        lines.append("%s=%s set for this push, the exporter's own marked "
                      "invocation" % (edition_guard.EXPORT_ENV,
                                       edition_guard.EXPORT_MARK))
        push = subprocess.run(
            ["git", "push", remote, "%s:%s" % (branch, branch)],
            cwd=d, capture_output=True, text=True, env=env, timeout=120)
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
        lines.append("PUSHED: one commit appended to %s %s" % (remote, branch))
        if tag:
            # Release identity (productization directive A4, founder-ordered
            # 2026-08-31): a release needs a tag on the public repository,
            # the edition guard correctly refuses every direct session push
            # there, so the ONE allowed invocation grows the ONE extra ref a
            # release needs. Annotated, points at the export commit pushed
            # just above, same marked env, never forced: an existing tag of
            # the same name refuses rather than moves.
            tagged = _run(["git", "tag", "-a", tag, "-m",
                           "Brother %s" % tag.lstrip("v"), branch], d)
            if tagged.returncode != 0:
                lines.append("REFUSED: could not create tag %s locally (%s)"
                             % (tag, (tagged.stderr or "").strip()))
                return EXIT_REFUSED, lines
            tag_push = subprocess.run(
                ["git", "push", remote, "refs/tags/%s" % tag],
                cwd=d, capture_output=True, text=True, env=env, timeout=120)
            if (tag_push.stderr or "").strip():
                lines.append((tag_push.stderr or "").strip())
            if tag_push.returncode != 0:
                lines.append("REFUSED: the tag push was rejected; an "
                             "existing %s is never moved, this exporter "
                             "never --force pushes" % tag)
                return EXIT_REFUSED, lines
            lines.append("TAGGED: %s points at the export commit just "
                         "appended" % tag)
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
                          "tag_time_checks: its release note is stamped "
                          "and every product's own verify-install.sh "
                          "passes on it.")
    ap.add_argument("--bootstrap", action="store_true",
                     help="with --push only: when the remote has no "
                          "branch at all, start it with this export as "
                          "its first commit (the clean-extraction route "
                          "of the 2026-09-03 decision); refused when the "
                          "remote has any branch, and refused without "
                          "--push")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    allowlist = load_allowlist(args.allowlist)
    if allowlist is None:
        print("NO-DATA: no allowlist at %s; refusing to export anything."
              % (args.allowlist or DEFAULT_ALLOWLIST))
        return EXIT_NODATA

    with tempfile.TemporaryDirectory(prefix="brother-export-") as export_dir, \
         tempfile.TemporaryDirectory(prefix="brother-export-identity-") as identity_dir:
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
        gates_ok, gate_lines = run_gates(export_dir, identity_dir)
        for line in gate_lines:
            print(line)

        if not gates_ok:
            print("REFUSED: at least one gate did not clear on the "
                  "candidate export tree. Nothing was pushed.")
            return EXIT_REFUSED

        print("CLEAR: every gate passed on the candidate export tree "
              "(%d file/path entr%s)"
              % (len(copied), "y" if len(copied) == 1 else "ies"))

    if not args.push:
        if args.tag:
            print("REFUSED: --tag only means something with --push; a "
                  "dry run never creates a ref anywhere")
            return EXIT_REFUSED
        if args.bootstrap:
            print("REFUSED: --bootstrap only means something with --push")
            return EXIT_REFUSED
        print("DRY-RUN: no push performed. Pass --push (with --remote) "
              "to push for real.")
        return EXIT_OK

    if not args.remote:
        print("NO-DATA: --push requires --remote")
        return EXIT_NODATA

    code, lines = push_appended(allowlist, args.remote, args.branch,
                                args.root, tag=args.tag,
                                bootstrap=args.bootstrap)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
