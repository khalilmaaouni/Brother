#!/usr/bin/env python3
"""vault_bridge: read-only, advisory access to the estate's own written memory.

Two doors, both subprocess calls into tools that already exist outside this
product, never a direct open() on anything under the vault:

    recall_context(statement)        -- what has the estate already written
                                         about this, via bm_vault.py recall
    propose_lesson(claim, review, claim_path=None)
                                      -- file a MISSED score as a lesson
                                         candidate, via bm_vault_intake.py
                                         capture. Human approved is always
                                         False: this door only proposes.

Every failure path returns NO-DATA with a reason. Neither function raises.
This module never decides anything a gate reads: bds.py's check() never
calls it, and receipt() calls recall_context() only after check() has
already returned its verdict, so a vault lesson can never move a gate.

The tools this calls live outside this product's own tree (the estate's
BrotherMode toolset). Resolved tool paths, same posture as bds.py's own
"far side of the seam" isolation: this module reaches out to call a tool by
path, it never reaches in to read or write a file under the vault itself.
"""
import json
import os
import re
import subprocess
import sys

def _version_key(path):
    """Sort key for a plugin cache path by its version segment (1.0.12 > 1.0.9)."""
    parts = []
    for seg in path.split(os.sep):
        if seg.replace(".", "").isdigit() and "." in seg:
            parts = [int(x) for x in seg.split(".")]
    return parts


def _root_tools(root):
    """Known package layouts, without deciding which client owns a root."""
    return [os.path.join(root, "runtime", "hooks", "brothermode", "tools"),
            os.path.join(root, "tools"),
            os.path.join(root, "products", "brothermode", "tools")]


def _cached_tools(config):
    import glob
    pattern = os.path.join(config, "plugins", "cache", "*", "brother", "*",
                           "runtime", "hooks", "brothermode", "tools")
    return sorted(glob.glob(pattern), key=lambda p: (_version_key(p), p), reverse=True)


def _load_paths():
    """Locate the shipped resolver; its APIs alone choose client and config.

    Both default homes are searched only to bootstrap the shared module.
    Finding a module there does not select that home for the Vault.
    A standalone product without Brother retains explicit tool overrides.
    """
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    explicit_tools = os.environ.get("BROTHERDS_VAULT_TOOLS")
    if explicit_tools:
        candidates.append(os.path.join(explicit_tools, "brother_paths.py"))
    for name in ("BROTHER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"):
        root = os.environ.get(name)
        if root:
            for sub in ("runtime", "tools", "scripts"):
                candidates.append(os.path.join(root, sub, "brother_paths.py"))
            candidates.extend(os.path.join(d, "brother_paths.py") for d in _root_tools(root))
    candidates.append(os.path.join(os.path.dirname(here), "brothermode", "tools", "brother_paths.py"))
    configs = [os.environ.get(k) for k in ("BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")]
    configs += [os.path.expanduser("~/.codex"), os.path.expanduser("~/.claude")]
    for config in filter(None, configs):
        for directory in _cached_tools(config):
            candidates.append(os.path.join(directory, "brother_paths.py"))
            # The bundle also ships the resolver beside its runtime entry point.
            runtime = os.path.dirname(os.path.dirname(os.path.dirname(directory)))
            candidates.append(os.path.join(runtime, "brother_paths.py"))
        candidates.append(os.path.join(config, "skills", "brothermode", "tools", "brother_paths.py"))
    for filename in dict.fromkeys(candidates):
        if not os.path.isfile(filename):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_bds_brother_paths", filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if all(callable(getattr(module, name, None)) for name in ("plugin_root", "config_dir", "config_path")):
                return module
        except (OSError, ImportError, SyntaxError, AttributeError, ValueError):
            continue
    return None


_PATHS = _load_paths()


def _tools_dir():
    """Explicit tools, resolved package, sibling, then the client's installs."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    explicit = os.environ.get("BROTHERDS_VAULT_TOOLS")
    if explicit:
        candidates.append(os.path.abspath(os.path.expanduser(explicit)))
    if _PATHS is not None and any(os.environ.get(name) for name in
                                   ("BROTHER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT")):
        candidates.extend(_root_tools(_PATHS.plugin_root()))
    candidates.append(os.path.join(os.path.dirname(here), "brothermode", "tools"))
    pattern = None
    if _PATHS is not None:
        config = _PATHS.config_dir()
        candidates.extend(_cached_tools(config))
        candidates.append(os.path.join(config, "skills", "brothermode", "tools"))
        pattern = os.path.join(config, "plugins", "cache", "*", "brother", "<version>",
                               "runtime", "hooks", "brothermode", "tools")
    for directory in dict.fromkeys(candidates):
        if os.path.isfile(os.path.join(directory, "bm_vault.py")):
            return directory, candidates
    reason = pattern or "shared brother_paths resolver unavailable; configure BROTHERDS_VAULT_TOOLS"
    return None, candidates + [reason]


_TOOLS, _SEARCHED = _tools_dir()
VAULT_RECALL_TOOL = os.path.join(_TOOLS or "", "bm_vault.py") if _TOOLS else (
    "NOT FOUND (searched: %s)" % ", ".join(_SEARCHED))
VAULT_INTAKE_TOOL = os.path.join(_TOOLS or "", "bm_vault_intake.py") if _TOOLS else (
    "NOT FOUND (searched: %s)" % ", ".join(_SEARCHED))

def _env_seconds(name, default):
    """A positive number of seconds from the environment, else the default.
    On a loaded machine the Vault's recall can need more than 5 seconds;
    BROTHERDS_RECALL_TIMEOUT_S raises the budget without a code change."""
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return v if v > 0 else default


RECALL_TIMEOUT_S = _env_seconds("BROTHERDS_RECALL_TIMEOUT_S", 5)
CAPTURE_TIMEOUT_S = 10

# Matches the "  <title>  [kind, source]" and "  WITHHELD (...)  <title>
# [kind, source]" lines bm_vault.py's own _print_hits() prints, whether the
# note was served or withheld. A withheld note was still surfaced (its title
# and reason are shown); it is never treated as evidence, which is why the
# card always reports "0 treated as evidence" regardless of what this finds.
_TITLE_LINE = re.compile(r"^  (?!\s)(?:WITHHELD \([^)]*\)\s+)?(.+?)  \[[^\]]*\]$",
                         re.MULTILINE)


def _nodata(why):
    return {"state": "NO-DATA", "why": why}


def _config_path():
    """Return the path to bm_vault.json config file."""
    return _PATHS.config_path("bm_vault.json") if _PATHS is not None else None


def _load_config():
    """Load the bm_vault.json config, or return {} if absent or unreadable."""
    config_file = _config_path()
    if config_file is None:
        return {}
    try:
        with open(config_file, encoding="utf-8") as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_vault():
    """Resolve the vault path following the estate's precedence order.

    Precedence, highest first:
      1. BROTHERDS_VAULT environment variable
      2. BM_VAULT_ROOT environment variable
      3. BROTHERMODE_VAULT environment variable
      4. "vault" key in the shared resolver's bm_vault.json config file
      5. None (no vault configured)

    Returns the vault path string, or None if no vault is configured.
    Never guesses or returns a default path.
    """
    vault = os.environ.get("BROTHERDS_VAULT")
    if vault:
        return vault

    vault = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    if vault:
        return vault

    config = _load_config()
    config_vault = config.get("vault")
    if isinstance(config_vault, str) and config_vault:
        return config_vault

    return None


def recall_context(statement):
    """What the estate's vault already has on this statement, or NO-DATA.

    Never raises. A timeout, a missing tool, a bad exit code, or an
    unparseable answer all degrade to NO-DATA naming why; an exit code of 1
    (bm_vault's own "nothing matched") is a real, honest empty answer, not a
    failure, so it comes back OK with count 0.
    """
    if not statement:
        return _nodata("no statement to recall against")
    if not os.path.exists(VAULT_RECALL_TOOL):
        return _nodata("bm_vault.py not found at %s" % VAULT_RECALL_TOOL)

    vault = resolve_vault()
    if not vault:
        return _nodata("vault not configured (checked BROTHERDS_VAULT, BM_VAULT_ROOT, BROTHERMODE_VAULT, %s)" % (_config_path() or "shared config resolver unavailable"))

    if not os.path.isdir(vault):
        return _nodata("vault path not found: %s" % vault)

    try:
        proc = subprocess.run(
            # --vault names the resolved vault: without it bm_vault answers from
            # whatever vault its own config points at, and BROTHERDS_VAULT is ignored.
            # --project boosts the estate's own BrotherDS notes over unrelated
            # projects: on a Vault with no master data lessons yet, plain lexical
            # recall surfaced a note from another project (2026-09-12).
            [sys.executable, VAULT_RECALL_TOOL, "recall", "--vault", vault,
             "--project", "brotherds",
             "--query", statement, "--limit", "5", "--fast"],
            capture_output=True, text=True, timeout=RECALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _nodata("recall timed out after %gs (raise BROTHERDS_RECALL_TIMEOUT_S)"
                       % RECALL_TIMEOUT_S)
    except OSError as exc:
        return _nodata("recall subprocess failed: %s" % exc)

    if proc.returncode == 0:
        titles = _TITLE_LINE.findall(proc.stdout or "")
        return {"state": "OK", "count": len(titles), "titles": titles}
    if proc.returncode == 1:
        # bm_vault's own "NO-DATA <header>, nothing matched" path: a real
        # answer, not a failure of this bridge.
        return {"state": "OK", "count": 0, "titles": []}
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = detail[-1] if detail else "exit %d" % proc.returncode
    return _nodata("recall exited %d: %s" % (proc.returncode, detail))


def _redact_figures(text):
    """Digit sequences replaced with a placeholder. The claim id is carried
    separately (see propose_lesson), never inside this redacted text, so a
    statement can be shown without smuggling its number back in through the
    one field meant to be figure-free."""
    return re.sub(r"\d[\d,]*(?:\.\d+)?", "[figure]", text or "")


def propose_lesson(claim, review, claim_path=None):
    """File a MISSED claim as a lesson candidate. The one intake door: this
    never opens a file under the vault itself, it only shells out to
    bm_vault_intake.py capture, which does. The vault path is never guessed:
    BROTHERDS_VAULT unset or empty is NO-DATA, full stop.
    """
    vault = os.environ.get("BROTHERDS_VAULT")
    if not vault:
        return _nodata("vault not configured")
    if not os.path.isdir(vault):
        return _nodata("vault path not found: %s" % vault)
    if not os.path.exists(VAULT_INTAKE_TOOL):
        return _nodata("bm_vault_intake.py not found at %s" % VAULT_INTAKE_TOOL)

    claim_id = claim.get("id", "UNKNOWN")
    claim_type = claim.get("claim_type") or claim.get("type")
    title_bits = ["MISSED", str(claim_id)]
    if claim_type:
        title_bits.append(str(claim_type))
    title = " ".join(title_bits)[:80]

    lines = ["claim: %s" % claim_id]
    if claim_type:
        lines.append("type: %s" % claim_type)
    lines.append("statement: %s" % _redact_figures(claim.get("statement", "")))
    cause = (review or {}).get("cause", "NO-DATA: cause not recorded")
    lines.append("miss reason: %s" % cause)
    lines.append("receipt: %s" % (claim_path or "NO-DATA: not recorded"))
    lines.append("human_approved: false")
    text = "\n".join(lines)
    return _capture(vault, title, text)


def propose_recurring(title, body):
    """File a gate the team keeps failing as a lesson candidate. Same intake
    door and the same explicit-vault rule as propose_lesson: BROTHERDS_VAULT
    unset is NO-DATA, and a person decides whether it becomes a rule."""
    vault = os.environ.get("BROTHERDS_VAULT")
    if not vault:
        return _nodata("vault not configured (set BROTHERDS_VAULT to file lessons)")
    if not os.path.isdir(vault):
        return _nodata("vault path not found: %s" % vault)
    if not os.path.exists(VAULT_INTAKE_TOOL):
        return _nodata("bm_vault_intake.py not found at %s" % VAULT_INTAKE_TOOL)
    return _capture(vault, title[:80], body + "\nhuman_approved: false")


def _capture(vault, title, text):
    """The one call into bm_vault_intake.py capture. Never raises."""
    try:
        proc = subprocess.run(
            [sys.executable, VAULT_INTAKE_TOOL, "capture",
             "--vault", vault, "--by", "brotherds",
             "--expiry-class", "lesson-candidate", "--title", title],
            input=text, capture_output=True, text=True,
            timeout=CAPTURE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _nodata("capture timed out after %ds" % CAPTURE_TIMEOUT_S)
    except OSError as exc:
        return _nodata("capture subprocess failed: %s" % exc)

    if proc.returncode == 0:
        m = re.search(r"id=(\S+)", proc.stdout or "")
        if m:
            return {"state": "OK", "id": m.group(1)}
        return _nodata("capture exited 0 but printed no id")
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = detail[-1] if detail else "exit %d" % proc.returncode
    return _nodata("capture refused: %s" % detail)
