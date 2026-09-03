"""Install BrotherSBE's local footprint into a target repository: a config
file, a dossier directory, an optional copy of the consumer CI, and a receipt
naming exactly what was written and how to remove it.

Idempotent is the whole point. `sbe init --apply` run twice must change
nothing the second time, and say so, because an install step that silently
does something different on a rerun is a step nobody can trust in a script.
That works here because every proposal is DETERMINISTIC content (no
timestamp, no run id) compared byte for byte against what is already on
disk: the second run finds every proposal already matches and writes
nothing, and the receipt -- the one artifact that legitimately does carry a
timestamp -- is then left untouched rather than rewritten with a new one,
because nothing happened this run for it to describe.

The generated config also carries the team profile: `.sbe/team-profile.json`
in the TARGET repository when it has one, otherwise this installation's own
copy, read by `load_team_profile()` below and folded into `.brothersbe/
config.json` field by field. Exactly one field set is supported (dossierRoot,
vaultPathPattern, ci, codeGuideDepth, schemaVersion -- the same five names
install.sh has always advertised reading). Anything else the profile file
contains is REJECTED BY NAME rather than silently dropped, because a field a
team added and never saw applied is a worse failure than a field that never
existed: the first looks like it worked.

`--dry-run` (the default, wired in cli.py) shows every intended mutation as a
diff and writes nothing; `--apply` writes. Refuses outside a git repository,
naming the reason, because there is nowhere for the config, the dossier
directory or the receipt to be versioned, and installing outside version
control is not this command's job to guess is wanted.
"""
import io
import json
import os
import re
import subprocess
import time

from . import SCHEMA_VERSION, repo_root, version

CONFIG_PATH = ".brothersbe/config.json"
#: Where the dossier directory lives when no profile names a different
#: dossierRoot -- the same default `_config_content()` writes into
#: `.brothersbe/config.json` when a profile is silent about that field.
DEFAULT_DOSSIER_ROOT = "design"
#: The default-case marker only. A profile naming a different dossierRoot
#: changes where the marker actually goes; `_dossier_marker(profile)` below
#: computes the path this run actually uses, from the SAME resolved root
#: `_config_content()` writes into the config, so the two can never name
#: different places. This constant exists for the default case and for
#: anything that still reads it expecting "design/.gitkeep".
DOSSIER_MARKER = DEFAULT_DOSSIER_ROOT + "/.gitkeep"
RECEIPT_PATH = ".brothersbe/install-receipt.json"
GITIGNORE_PATH = ".gitignore"
#: One line above the ignore entry, explaining why it is there before a
#: reader has to go looking: the receipt records this machine's absolute
#: install path, a local, personal fact that has no business being tracked.
GITIGNORE_COMMENT = ("# sbe init: the install receipt below records this machine's absolute "
                     "path, so it stays out of git")
CONSUMER_WORKFLOW_PATH = ".github/workflows/consumer-check.yml"
CONSUMER_ACTION_PATH = ".github/actions/sbe-consumer/action.yml"

#: Bitbucket reads this filename from the repository ROOT and nowhere else,
#: which is why there is one path here against two for GitHub.
CONSUMER_BITBUCKET_PATH = "bitbucket-pipelines.yml"

#: Where this installation's own copies of the consumer CI templates live,
#: relative to `repo_root()`. Read at plan time rather than duplicated as a
#: string constant in this module, so editing the shipped workflow or action
#: is the only place that content has to change.
_CONSUMER_WORKFLOW_SOURCE = ".github/workflows/consumer-check.yml"
_CONSUMER_ACTION_SOURCE = ".github/actions/sbe-consumer/action.yml"
_CONSUMER_BITBUCKET_SOURCE = "ci/bitbucket-pipelines.yml"


def _origin_host(root):
    """The origin remote's host for the repository at `root`, lowercased, or
    None when there is no origin, no git, or a url this cannot parse. None is
    "could not look" and the caller says so out loud; it never becomes a
    silent assumption about which host a team is on."""
    try:
        proc = subprocess.run(["git", "remote", "get-url", "origin"],
                              cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    if not url:
        return None
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/]+@)?([^/:]+)", url)
    if m:
        return m.group(1).lower()
    m = re.match(r"^(?:[^@]+@)([^:]+):", url)          # scp-like: git@host:path
    return m.group(1).lower() if m else None

#: Same relative path in the target repository and in this installation:
#: `load_team_profile()` tries the target first (a team's own committed
#: answer) and falls back to this copy (the distribution's default answer),
#: so one constant serves both lookups.
TEAM_PROFILE_PATH = os.path.join(".sbe", "team-profile.json")

#: The exactly-one field set `sbe init` understands, in the same order
#: install.sh has always advertised reading (its own apply_team_profile
#: comment, "dossierRoot, vaultPathPattern, ci, codeGuideDepth, and
#: schemaVersion"). A field outside this tuple is named and rejected rather
#: than merged in, because the alternative -- accepting whatever keys show up
#: -- means a typo in a team's profile changes nothing and says nothing.
SUPPORTED_TEAM_PROFILE_FIELDS = ("dossierRoot", "vaultPathPattern", "ci",
                                 "codeGuideDepth", "schemaVersion")


class NotAGitRepository(Exception):
    """Raised rather than returning a silent no-op: an install with nowhere
    to be versioned is a different finding from an install that is already
    done, and folding them together would hide the reason from whoever reads
    the refusal."""


def _git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def refusal_reason(root):
    """None, or the reason `sbe init` must refuse this root outright."""
    code, out, err = _git(["rev-parse", "--is-inside-work-tree"], root)
    if code != 0 or out.strip() != "true":
        return ("%s is not inside a git repository (%s); sbe init refuses because there is "
                "nowhere for the config, the dossier directory or the install receipt to be "
                "versioned, and installing outside version control is not this command's "
                "job to guess is wanted"
                % (root, (err or out).strip() or "git could not answer"))
    return None


def load_team_profile(root):
    """The team profile this `root` gets installed with, and exactly what
    happened reading it: never a bare dict of values, because a caller that
    only sees the values cannot tell "the team asked for this" from "nobody
    asked and this is the built-in default", and install.sh's installation
    report (REQUIRED to name what was requested, applied and rejected,
    separately) needs that difference visible.

    Tries `root`'s own `.sbe/team-profile.json` first -- a team's committed,
    same-for-everyone answer -- then this installation's copy of the same
    path, so a repository with no profile of its own still gets sensible
    defaults instead of an error. Returns a dict:

      `path`      the file actually read, or None when neither carries one
      `source`    "target repository", "distribution", or None to match
      `requested` every field name the file contains, sorted
      `applied`   the SUPPORTED_TEAM_PROFILE_FIELDS subset, name -> value
      `rejected`  field names the file contains that are outside the
                  supported set, named rather than merged in or dropped
      `problem`   why no field could be read (unreadable file, invalid
                  JSON, not a JSON object), or None
    """
    candidates = [(os.path.join(root, TEAM_PROFILE_PATH), "target repository"),
                 (os.path.join(repo_root(), TEAM_PROFILE_PATH), "distribution")]
    for path, source in candidates:
        if not os.path.exists(path):
            continue
        try:
            with io.open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except (IOError, OSError) as exc:
            return {"path": path, "source": source, "requested": [], "applied": {},
                    "rejected": [], "problem": "%s could not be read (%s)" % (path, exc)}
        try:
            data = json.loads(raw)
        except ValueError as exc:
            return {"path": path, "source": source, "requested": [], "applied": {},
                    "rejected": [], "problem": "%s is not valid JSON (%s)" % (path, exc)}
        if not isinstance(data, dict):
            return {"path": path, "source": source, "requested": [], "applied": {},
                    "rejected": [], "problem": "%s does not contain a JSON object" % path}
        requested = sorted(data.keys())
        applied = dict((k, data[k]) for k in requested if k in SUPPORTED_TEAM_PROFILE_FIELDS)
        rejected = [k for k in requested if k not in SUPPORTED_TEAM_PROFILE_FIELDS]
        return {"path": path, "source": source, "requested": requested, "applied": applied,
                "rejected": rejected, "problem": None}
    return {"path": None, "source": None, "requested": [], "applied": {}, "rejected": [],
            "problem": "no %s found in %s or in this installation (%s)"
                       % (TEAM_PROFILE_PATH, root, repo_root())}


def _resolved_dossier_root(profile):
    """The one dossierRoot value this run uses, computed in exactly one
    place so `_config_content()` (what the config CLAIMS) and
    `_dossier_marker()` (what actually gets created) read the same answer
    and can never disagree: a profile supplying "blueprints" must produce
    both a config naming "blueprints" AND a `blueprints/.gitkeep` on disk,
    never a config naming one root while the marker sits under another.
    Falls back to DEFAULT_DOSSIER_ROOT ("design") when the profile is
    silent, or supplies an empty string, about dossierRoot.
    """
    root = profile["applied"].get("dossierRoot") or DEFAULT_DOSSIER_ROOT
    root = root.rstrip("/") or DEFAULT_DOSSIER_ROOT
    return root


def _dossier_marker(profile):
    """The `.gitkeep` marker path for THIS run's resolved dossier root (see
    `_resolved_dossier_root()`), so the directory `sbe init` actually
    creates always matches the `dossierRoot` its own config.json claims."""
    return "%s/.gitkeep" % _resolved_dossier_root(profile)


def _config_content(profile):
    """The proposed `.brothersbe/config.json` body. `tool` and `toolVersion`
    name this installation and are never a team's call, so they stay fixed;
    `schemaVersion` and `dossierRoot` keep their long-standing defaults when
    the profile is silent about them (SCHEMA_VERSION and DEFAULT_DOSSIER_ROOT,
    the exact values this function hardcoded before team profiles were read
    at all, so a repository with no profile installs exactly as it always
    did); every other SUPPORTED_TEAM_PROFILE_FIELDS name the profile
    actually supplied is written through as-is, never invented.
    """
    applied = profile["applied"]
    payload = {
        "schemaVersion": applied.get("schemaVersion", SCHEMA_VERSION),
        "tool": "sbe init",
        "toolVersion": version(),
        "dossierRoot": _resolved_dossier_root(profile),
    }
    for key in ("vaultPathPattern", "ci", "codeGuideDepth"):
        if key in applied:
            payload[key] = applied[key]
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _read_template(rel):
    """The content of one of this installation's own shipped files, or
    (None, reason) when it cannot be read. Read from `repo_root()`, the
    BrotherSBE installation running this command, which may be a different
    directory from the repository being initialized.
    """
    full = os.path.join(repo_root(), rel)
    try:
        with io.open(full, encoding="utf-8") as fh:
            return fh.read(), None
    except (IOError, OSError) as exc:
        return None, ("this installation carries no readable %s (%s), so the consumer CI "
                      "copy cannot be proposed" % (rel, exc))


def _gitignore_content(root):
    """The full `.gitignore` body this run proposes: whatever is already on
    disk, untouched, plus the receipt line and its comment appended when
    that line is not already present.

    Appending rather than owning the file is what makes this proposal
    compare identical, like every other one, against exactly what is
    already there: once the line exists this returns the file back
    unchanged, so a rerun reports it identical instead of proposing a
    second copy. It is also why `apply()` never lists `.gitignore` in the
    receipt's own written set -- this command only ever adds one line to a
    file it does not own, and an uninstall instruction to delete that file
    would take every other line in it too.
    """
    full = os.path.join(root, GITIGNORE_PATH)
    try:
        with io.open(full, encoding="utf-8") as fh:
            existing = fh.read()
    except (IOError, OSError):
        existing = None
    if existing is not None and RECEIPT_PATH in existing.splitlines():
        return existing
    prefix = existing or ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + GITIGNORE_COMMENT + "\n" + RECEIPT_PATH + "\n"


def plan(root, with_consumer_ci=False):
    """The mutations `sbe init` would make, each compared against what is
    already on disk. Read-only: never writes, so `--dry-run` can call this
    directly.

    Loads the team profile (see `load_team_profile()`) once and folds its
    supported fields into the proposed config; any field the profile
    contains outside that supported set is named in `warnings` here rather
    than only inside `apply()`'s result, so a plain `--dry-run` shows the
    rejection before anything is written, not only after.
    """
    profile = load_team_profile(root)
    mutations = [(CONFIG_PATH, _config_content(profile)), (_dossier_marker(profile), ""),
                (GITIGNORE_PATH, _gitignore_content(root))]
    warnings = []
    if profile["rejected"]:
        warnings.append(
            "team profile at %s named field(s) sbe init does not support: %s (supported: "
            "%s); rejected by name, not applied"
            % (profile["path"], ", ".join(profile["rejected"]),
               ", ".join(SUPPORTED_TEAM_PROFILE_FIELDS)))
    if profile["problem"] and profile["path"] is not None:
        warnings.append("team profile could not be applied: %s; config falls back to "
                        "built-in defaults" % profile["problem"])
    if with_consumer_ci:
        # WHICH HOST'S CI, decided by the target's own origin remote rather
        # than assumed. Installing a GitHub Actions workflow into a
        # Bitbucket-hosted repository put a file there that can never fire,
        # with no warning, while the Bitbucket template that was the right
        # answer sat unoffered in ci/. A host this cannot resolve gets the
        # GitHub pair (the reference implementation) AND a warning saying so,
        # never a silent guess.
        host = _origin_host(root)
        if host == "bitbucket.org":
            content, problem = _read_template(_CONSUMER_BITBUCKET_SOURCE)
            if problem:
                warnings.append(problem)
            else:
                mutations.append((CONSUMER_BITBUCKET_PATH, content))
        else:
            if host is None:
                warnings.append(
                    "the origin remote could not be read, so the GitHub consumer CI "
                    "files were proposed as the reference implementation; if this "
                    "repository is hosted on Bitbucket, take ci/bitbucket-pipelines.yml "
                    "from this installation instead and put it at your repository root")
            for rel, source in ((CONSUMER_WORKFLOW_PATH, _CONSUMER_WORKFLOW_SOURCE),
                                (CONSUMER_ACTION_PATH, _CONSUMER_ACTION_SOURCE)):
                content, problem = _read_template(source)
                if problem:
                    warnings.append(problem)
                else:
                    mutations.append((rel, content))

    proposals = []
    for rel, content in mutations:
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
        proposals.append({"path": rel, "content": content, "exists": exists,
                          "identical": identical})
    return proposals, warnings


def _read_receipt(full):
    if not os.path.exists(full):
        return None
    try:
        with io.open(full, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, IOError, OSError):
        return None


def apply(root, with_consumer_ci=False):
    """Write every non-identical proposal, then write or refresh the
    install receipt naming every artifact path this installation has
    written (across this call and any prior one the existing receipt
    recorded). Raises `NotAGitRepository` rather than writing anything when
    `root` is not inside a git repository.

    Returns a dict: `written` (the artifact paths actually written this
    call, plus the receipt path when the receipt itself changed), `skipped`
    (proposal paths that already matched what was on disk, so nothing was
    written for them this call), `skippedAsNoop` (True when nothing at all
    needed writing), `receipt` (the receipt content, whether freshly written
    or the one already on disk), `warnings` (why an optional proposal, like
    the consumer CI copy, could not be made, and why any team-profile field
    was rejected), `teamProfile` (the `load_team_profile()` result this call
    used, so a caller like install.sh can report what was requested, applied
    and rejected by name without re-reading the file itself).

    `.gitignore` is written like any other proposal when the receipt line is
    missing (so it shows up in `written`), but it never enters the receipt's
    `writtenPaths` or `uninstallInstructions`: this command appended one line
    to a file it does not own, and telling someone to `rm -f .gitignore`
    would delete every other line a real project keeps in that file.
    """
    reason = refusal_reason(root)
    if reason:
        raise NotAGitRepository(reason)

    proposals, warnings = plan(root, with_consumer_ci)
    # plan() already loaded the profile once to build the config proposal;
    # loaded again here rather than threaded through plan()'s return value,
    # because plan()'s two-value (proposals, warnings) return is read by
    # cli.py today and a third value would silently break that unpack.
    profile = load_team_profile(root)
    written = []
    skipped = []
    for item in proposals:
        if item["identical"]:
            skipped.append(item["path"])
            continue
        full = os.path.join(root, item["path"])
        directory = os.path.dirname(full)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(item["content"])
        written.append(item["path"])

    receipt_full = os.path.join(root, RECEIPT_PATH)
    if not written:
        return {"written": [], "skipped": skipped, "skippedAsNoop": True,
                "receipt": _read_receipt(receipt_full), "warnings": warnings,
                "teamProfile": profile}

    prior = _read_receipt(receipt_full) or {}
    prior_paths = list(prior.get("writtenPaths", []))
    # .gitignore is excluded here on purpose: it is a write this call made,
    # but not a file this installation owns, so it never enters the set an
    # uninstall instruction is generated from. See the docstring above.
    receipt_candidates = [p for p in written if p != GITIGNORE_PATH]
    all_written = prior_paths + [p for p in receipt_candidates if p not in prior_paths]
    # The receipt is itself a written file, so it names itself in the set it
    # records: uninstall instructions that omit the receipt are not exact.
    if RECEIPT_PATH not in all_written:
        all_written.append(RECEIPT_PATH)
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "tool": "sbe init",
        "toolVersion": version(),
        "installedAt": _iso(time.time()),
        "installedInto": os.path.abspath(root),
        "writtenPaths": all_written,
        "uninstallInstructions": ["rm -f %s" % p for p in all_written],
    }
    directory = os.path.dirname(receipt_full)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(receipt_full, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    return {"written": written + [RECEIPT_PATH], "skipped": skipped, "skippedAsNoop": False,
           "receipt": receipt, "warnings": warnings, "teamProfile": profile}
