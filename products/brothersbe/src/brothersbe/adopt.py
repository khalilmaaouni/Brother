"""Inspect a repository for BrotherSBE readiness, and propose what it is
missing, without silently changing anything.

The defect this exists for, in one sentence: `cli.py` used to refuse `sbe
adopt` outright, naming the reason as "the adoption doctor cannot yet tell a
protected repository from an unprotected one, and a readiness report that
omits that is worse than none." Saying nothing was safer than a report that
could not make that distinction.

THE KILL CRITERION THIS MODULE IS BUILT AROUND: the report must never claim a
protection is PRESENT when the GitHub API was never asked. Branch protection,
required status checks, and "review from a code owner is required" are
settings on the code review platform, not on the filesystem. Nothing in a git
clone can read them, and this module holds no GitHub credentials and asks for
none. So `adoption_report`'s `protections` list reports every one of those
three as UNVERIFIABLE-HERE, unconditionally, and names what reading it would
take (a GitHub token with repo scope, and admin rights on the repository)
rather than inferring PRESENT from a local proxy (a CODEOWNERS file exists) or
ABSENT from its absence. A CODEOWNERS file existing locally IS a fact this
tool can read, and it is reported separately, under `localFacts`, so the two
are never folded into one claim that neither earns on its own.

Stack detection reuses the SAME path patterns `sbe impact` already carries for
contract, migration and dbt-shaped files (`brothersbe.impact.DETECTORS`),
walked over the whole tree instead of a diff, so a pattern that means "this is
an OpenAPI document" in one tool means the same thing in the other. One rule,
one table, read twice.

Everything this module writes is a PROPOSAL. `--dry-run` (the default, in
cli.py) prints every proposed file as a unified diff and writes nothing at
all; `--apply` writes, never overwrites an existing file that differs without
`--force`, and a second `--apply` over unchanged proposals writes nothing and
says so, because the proposals themselves are deterministic: nothing about
them (dates, run ids) changes between two runs against the same tree.

The repository policy file this proposes is NOT wave 3's eventual schema.
Wave 3 (a repository policy file plus its own JSON schema, per the plugin
conversion plan) has not shipped as this module is written. What is proposed
here is a provisional shape, labelled as such in the file it writes, built
from what this module can already detect. Full limits: `docs/KNOWN-LIMITS.md`.
"""
import difflib
import io
import json
import os
import re

from . import SCHEMA_VERSION, version
from .impact import DETECTORS

#: Directories never worth walking for stack detection: vendored dependencies
#: or build output, never a repository's own first-party code. Pruned by
#: name, not by content, and that is a stated limit, not a silent one: a
#: project keeping first-party source inside a directory with one of these
#: names is under-detected. `docs/KNOWN-LIMITS.md` says so.
PRUNED_DIRS = frozenset([
    ".git", "node_modules", "vendor", "venv", ".venv", "dist", "build",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "target",
])

#: Extension -> language name, for the census this reports. Deliberately
#: small and named: an extension outside this map is counted under
#: `otherExtensionFiles` rather than guessed at, exactly as `sbe impact`
#: reports a file no detector covers under `unmeasured` rather than folding
#: it into a clean result.
LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".rb": "Ruby", ".go": "Go", ".java": "Java",
    ".kt": "Kotlin", ".rs": "Rust", ".php": "PHP", ".cs": "C#",
    ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".sql": "SQL", ".sh": "Shell",
}

#: The detector ids from `brothersbe.impact.DETECTORS` this module reuses to
#: recognize a contract, migration or dbt-shaped path. Reused rather than
#: reimplemented: `_STACK_PATTERNS` is read straight out of the same table
#: `sbe impact` runs against a diff, applied here against a full tree walk.
_STACK_DETECTOR_IDS = ("openapi", "asyncapi", "protobuf", "avro", "graphql",
                       "event-schema", "db-migration", "sql-ddl", "dbt-model")
_STACK_PATTERNS = [(det[0], det[3]) for det in DETECTORS if det[0] in _STACK_DETECTOR_IDS]

POLICY_PATH = ".brothersbe/policy.json"
CODEOWNERS_PATH = ".github/CODEOWNERS"

#: Two entries proposed unconditionally, because the adoption kit itself
#: creates both paths: `sbe adopt --apply` writes `.brothersbe/`, and `sbe
#: init --apply` writes `design/`. Filtering these by existence would make
#: the proposal depend on whether adopt has already run, and a second
#: `--apply` would then find its own first write changed the proposal.
_SELF_CREATED_PROTECTED = (
    ("policies", (".brothersbe/",),
     "this repository's BrotherSBE policy and config; sbe adopt --apply creates it"),
    ("dossiers", ("design/",),
     "the design dossier root sbe init creates and sbe design reads"),
)

#: The layout-dependent categories the adoption kit is asked to protect,
#: with the paths they live at in a repository shaped like this one. Each
#: path is proposed ONLY if it exists under the target root: the first
#: external-proof round (docs/EXTERNAL-PROOF-2026-07-31.md) showed every one
#: of these is a ghost in a foreign clone, and a CODEOWNERS line protecting
#: a path that does not exist protects nothing while looking like it does.
#: A category with no surviving path is named in the policy's `_notProposed`
#: note instead of silently vanishing; `docs/ADOPTION.md` says so.
_LAYOUT_PROTECTED_PATHS = (
    ("manifest", (".claude-plugin/plugin.json",), "the plugin manifest"),
    ("hooks", ("hooks/",), "the write-fence and session hooks"),
    ("evidenceSchemas", ("src/brothersbe/evidence.py", "src/brothersbe/__init__.py"),
     "where the evidence schema version and required fields are declared"),
    ("workflows", (".github/workflows/", ".github/actions/"), "product and consumer CI"),
    ("releaseFiles", ("VERSION", "CHANGELOG.md", "CHECKSUMS.sha256"), "release identity"),
)

_NOT_PROPOSED_NOTE = (
    "Categories listed here name paths that do not exist under this root, so nothing "
    "was proposed for them: a protection rule over a ghost path protects nothing while "
    "looking like it does. If this repository keeps the equivalent files elsewhere, add "
    "the real paths to protectedPaths by hand before applying."
)


def _walk_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, root).replace(os.sep, "/")


def detect_stack(root):
    """A census of what is in the tree: languages by extension, and which of
    `sbe impact`'s own path patterns already match something here. Detected
    from files, not asked, matching the inspection order `skills/adopt/
    SKILL.md` already documents for this command.
    """
    lang_counts, other_count = {}, 0
    matched = {}
    workflows_present = False
    for rel in _walk_files(root):
        low = rel.lower()
        ext = os.path.splitext(low)[1]
        if ext in LANGUAGE_EXTENSIONS:
            lang = LANGUAGE_EXTENSIONS[ext]
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        elif ext:
            other_count += 1
        for det_id, pattern in _STACK_PATTERNS:
            if re.search(pattern, low):
                matched.setdefault(det_id, []).append(rel)
        if low.startswith(".github/workflows/") and low.endswith((".yml", ".yaml")):
            workflows_present = True
    return {
        "languages": dict(sorted(lang_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "otherExtensionFiles": other_count,
        "matchedPaths": matched,
        "hasMigrations": bool(matched.get("db-migration")),
        "hasDbtModels": bool(matched.get("dbt-model")),
        "hasApiContracts": bool(matched.get("openapi") or matched.get("asyncapi")
                                or matched.get("protobuf") or matched.get("avro")
                                or matched.get("graphql") or matched.get("event-schema")),
        "hasCiWorkflows": workflows_present,
    }


def _partition_by_existence(root, paths):
    """(present, missing, checked): each proposed path split by whether it
    exists under `root`, a trailing-slash path checked as a directory and
    any other as a file. Three values, never two: `checked` is the count
    examined, so a caller can tell "everything was missing" apart from
    "nothing was checked at all".
    """
    present, missing = [], []
    for rel in paths:
        target = os.path.join(root, rel.rstrip("/"))
        if rel.endswith("/"):
            (present if os.path.isdir(target) else missing).append(rel)
        else:
            (present if os.path.isfile(target) else missing).append(rel)
    return present, missing, len(present) + len(missing)


def propose_policy(root, detected):
    """The provisional repository policy `sbe adopt` proposes. Deterministic
    given the tree at `root` and `detected`: no timestamp or run id, so two
    runs against the same tree propose byte-identical content, which is what
    lets a second `--apply` recognize there is nothing left to write. The
    only paths proposed are the two the kit itself creates and the layout
    paths that actually exist under `root`; a category that survives with
    fewer paths, or not at all, has its missing paths named under
    `_notProposed` rather than proposed as ghosts.
    """
    protected = {}
    not_proposed = {}
    for key, paths, why in _SELF_CREATED_PROTECTED:
        protected[key] = {"paths": list(paths), "why": why}
    for key, paths, why in _LAYOUT_PROTECTED_PATHS:
        present, missing, _checked = _partition_by_existence(root, paths)
        if present:
            protected[key] = {"paths": present, "why": why}
        if missing:
            not_proposed[key] = {"missingPaths": missing, "why": why}
    if detected["hasMigrations"]:
        protected["migrations"] = {
            "paths": sorted(set(os.path.dirname(p) + "/" for p in
                                detected["matchedPaths"].get("db-migration", []))),
            "why": "a migration directory this scan detected; schema changes other code "
                   "and queries depend on",
        }
    if detected["hasDbtModels"]:
        protected["dbtModels"] = {
            "paths": sorted(set(os.path.dirname(p) + "/" for p in
                                detected["matchedPaths"].get("dbt-model", []))),
            "why": "a dbt models directory this scan detected; warehouse models other "
                   "models and reports select from",
        }
    return {
        "schemaVersion": "0.1-provisional",
        "toolVersion": version(),
        "note": "BrotherSBE wave 3 (the repository policy file and its own JSON schema) had "
                "not shipped when this proposal was generated. This is a provisional shape "
                "sbe adopt proposes from what it detected in this tree, not that eventual "
                "schema, and it says so on every line a reader might otherwise take as final.",
        "dossierRoot": "design",
        "detectedStack": {
            "languages": detected["languages"],
            "hasMigrations": detected["hasMigrations"],
            "hasDbtModels": detected["hasDbtModels"],
            "hasApiContracts": detected["hasApiContracts"],
            "hasCiWorkflows": detected["hasCiWorkflows"],
        },
        "protectedPaths": protected,
        "_notProposed": {"note": _NOT_PROPOSED_NOTE, "categories": not_proposed},
    }


def propose_codeowners(policy):
    """`.github/CODEOWNERS` content, generated FROM `policy["protectedPaths"]`
    rather than a second hand-typed list, so the file GitHub reads and the
    policy this project reasons about cannot drift apart. `@REPLACE-ME` is a
    placeholder token: this module has no repository membership to read a
    real team or username from, and typing one in would be a guess dressed up
    as a proposal. `docs/ADOPTION.md` says to replace it before relying on
    this file.
    """
    lines = [
        "# Generated by `sbe adopt`. Review before committing.",
        "#",
        "# Protecting a path here means edits to it need review from the named owner IF",
        "# branch protection also requires review from code owners -- a GitHub setting",
        "# this file cannot turn on by existing. See docs/ADOPTION.md.",
        "#",
        "# Replace @REPLACE-ME with a real user or team before this file does anything.",
        "",
    ]
    dropped = sorted(policy.get("_notProposed", {}).get("categories", {}))
    if dropped:
        lines.append("# Not proposed (no such path under this root): %s. A rule over a"
                     % ", ".join(dropped))
        lines.append("# path that does not exist protects nothing; see policy.json's")
        lines.append("# _notProposed note for the exact paths that were checked.")
        lines.append("")
    for key, entry in policy["protectedPaths"].items():
        lines.append("# %s: %s" % (key, entry["why"]))
        for path in entry["paths"]:
            lines.append("%s @REPLACE-ME" % path)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def plan(root):
    """The proposals `sbe adopt` would write, each carrying a unified diff
    against what is already there (or against nothing, for a new file).
    Read-only: this function never writes anything, so `--dry-run` can call
    it directly and the caller decides whether anything gets written.
    """
    detected = detect_stack(root)
    policy = propose_policy(root, detected)
    codeowners = propose_codeowners(policy)
    files = {
        POLICY_PATH: json.dumps(policy, indent=2, sort_keys=True) + "\n",
        CODEOWNERS_PATH: codeowners,
    }
    proposals = []
    for rel in sorted(files):
        content = files[rel]
        full = os.path.join(root, rel)
        exists = os.path.exists(full)
        existing = None
        if exists and os.path.isfile(full):
            try:
                with io.open(full, encoding="utf-8") as fh:
                    existing = fh.read()
            except (IOError, OSError, UnicodeDecodeError):
                existing = None
        identical = exists and existing == content
        diff_text = None
        if not identical:
            diff_text = "".join(difflib.unified_diff(
                (existing or "").splitlines(True), content.splitlines(True),
                fromfile=rel if exists else "/dev/null", tofile=rel))
        proposals.append({"path": rel, "exists": exists, "identical": identical,
                          "content": content, "diff": diff_text})
    return {"detected": detected, "policy": policy, "proposals": proposals}


def write(root, force=False):
    """Write every proposal that is not already identical, refusing an
    existing file that differs unless `force`. Returns (written, skipped,
    planned): `written` and `skipped` are what the caller reports, `planned`
    is the same structure `plan()` returns, for a caller that wants to show
    the diff of what it just decided not to touch.
    """
    planned = plan(root)
    written, skipped = [], []
    for item in planned["proposals"]:
        rel = item["path"]
        if item["identical"]:
            skipped.append({"path": rel, "reason": "already matches the proposal; nothing "
                                                    "to write"})
            continue
        if item["exists"] and not force:
            skipped.append({"path": rel, "reason": "exists and differs from the proposal; "
                                                    "rerun with --force to overwrite, or "
                                                    "remove it first"})
            continue
        full = os.path.join(root, rel)
        directory = os.path.dirname(full)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(item["content"])
        written.append(rel)
    return written, skipped, planned


def adoption_report(root):
    """(protections, localFacts). See the module docstring: `protections`
    never reports PRESENT, because nothing this module can read from a
    filesystem proves a GitHub branch protection setting either way.
    """
    codeowners_rel = ".github/CODEOWNERS"
    codeowners_alt_rel = "CODEOWNERS"
    codeowners_present = (os.path.exists(os.path.join(root, codeowners_rel))
                          or os.path.exists(os.path.join(root, codeowners_alt_rel)))
    # BOTH hosts are looked for, because looking only where GitHub keeps its CI
    # and then reporting ABSENT told a Bitbucket team with a working pipeline
    # that they had no CI at all. Bitbucket reads `bitbucket-pipelines.yml` from
    # the repository ROOT and nowhere else, which is why there is one path here
    # and three under .github.
    consumer_ci_present = (
        os.path.exists(os.path.join(root, ".github", "workflows", "consumer-check.yml"))
        or os.path.exists(os.path.join(root, "bitbucket-pipelines.yml")))
    product_ci_present = os.path.exists(
        os.path.join(root, ".github", "workflows", "brothersbe-gates.yml"))
    is_git = os.path.isdir(os.path.join(root, ".git"))

    missing = ("a GitHub token with repo scope, plus admin rights on the target "
              "repository; this tool holds no credentials and asked for none")

    # NEVER PRESENT. Read the module docstring before touching this list: the
    # kill criterion this whole module exists to satisfy is that none of
    # these three ever reports PRESENT from a local filesystem read.
    protections = [
        {"name": "branch-protection", "status": "UNVERIFIABLE-HERE", "missing": missing,
         "detail": "whether the default branch requires a pull request, blocks force "
                   "pushes, or requires status checks lives in GitHub's branch protection "
                   "settings (Settings > Branches > Branch protection rules), not in this "
                   "clone."},
        {"name": "required-status-checks", "status": "UNVERIFIABLE-HERE", "missing": missing,
         "detail": "whether 'BrotherSBE gates' is a REQUIRED check, versus one that merely "
                   "runs beside the merge button, is a branch protection setting this tool "
                   "cannot read from a clone."},
        {"name": "codeowners-review-required", "status": "UNVERIFIABLE-HERE",
         "missing": missing,
         "detail": (("a CODEOWNERS file is present in this tree, but whether GitHub "
                     "REQUIRES review from a code owner before merge is a branch "
                     "protection setting, not a property this file carries.")
                    if codeowners_present else
                    ("no CODEOWNERS file was found in this tree, and whether review from "
                     "a code owner is required is a branch protection setting either way, "
                     "so the file's absence is reported separately below rather than "
                     "folded into this line."))},
    ]
    local_facts = [
        {"name": "git-repository", "status": "PRESENT" if is_git else "ABSENT",
         "detail": "checked for %s" % os.path.join(root, ".git")},
        {"name": "codeowners-file", "status": "PRESENT" if codeowners_present else "ABSENT",
         "detail": "checked %s and %s" % (codeowners_rel, codeowners_alt_rel)},
        {"name": "product-ci-workflow", "status": "PRESENT" if product_ci_present else "ABSENT",
         "detail": "checked .github/workflows/brothersbe-gates.yml"},
        {"name": "consumer-ci-workflow",
         "status": "PRESENT" if consumer_ci_present else "ABSENT",
         "detail": "checked .github/workflows/consumer-check.yml"},
    ]
    return protections, local_facts


def report(root):
    """The whole `sbe adopt` verdict: what would be proposed, and what this
    tool could and could not verify about protection. `--dry-run` prints this
    and writes nothing; `--apply` calls `write()` first and then re-reports.
    """
    planned = plan(root)
    protections, local_facts = adoption_report(root)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "tool": "sbe adopt",
        "toolVersion": version(),
        "root": os.path.abspath(root),
        "detectedStack": planned["detected"],
        "proposals": [{"path": p["path"], "exists": p["exists"], "identical": p["identical"],
                       "diff": p["diff"]} for p in planned["proposals"]],
        "notProposed": planned["policy"]["_notProposed"],
        "protections": protections,
        "localFacts": local_facts,
    }
