#!/usr/bin/env python3
"""host_live_verify.py: the HP1 evidence verifier and the cut gate (HP1.b, spec docs/plan/specs/HP1.md section 5.3).

WHY THIS EXISTS. The witness (host_hook_trace.py) and the driver (host_live_proof.py) record one row per hook fire a
host itself started. This verifier is what the cut reads: it re-derives every fact it can from the row and the bytes
beside it and trusts no boolean a writer stored. Raw files are re-hashed, verdicts re-parsed with the one VERDICT_RULES
(host_live_proof.parse_verdict), probe labels re-derived with the one labeller (host_live_proof.probe_of), the host
ancestor and the launch chain recomputed from parent_chain and host_roots, the login state and the model turn re-read
from the envelope bytes, and the candidate (plugin_tree_sha256, the last commit to plugin, bundle or products, the
committed witness, tool, hook script and hook file blobs) read from git at the checkout.

"Signed in" means the host had a live account session (REQ-HP1-SIGNED). It is NEVER a cryptographic signature of a
row: the rows carry none (spec section 13, question 4).

Verdicts (the donecheck_units convention): 0 GREEN, 1 RED, 3 NO-DATA, and one printed line naming the host and the
condition; the deciding hosts default to DECIDING_HOSTS_1_1_0 and each is judged on its own, so a line short of GREEN
names every deciding host's own verdict (owner decision 2026-10-03: Antigravity is reported, never blocks).
A missing host, a missing probe, an unreadable candidate or a gone installed hook file is NO-DATA; every
other refusal is RED. Only the newest run_id per host is judged; older runs and rows of a named extra host are ignored
and reported. EXPECTED_GUARDED_WRITE is the one table the guarded write is judged by (owner decision 1, spec 13, as
amended by the owner ruling of 2026-10-04 hp1-guarded-write): no shipped hook guards a write outside the workspace on any
host in 1.1.0, so every entry expects the shipped answer, never a deny; changing an entry back to deny is the flip (the
1.1.1 guard). RULED_NO_DATA (owner rulings 2026-10-04 hp1-codex-door and 2026-10-05 hp1-claude-door) names the probes a host cannot
record in 1.1.0, door and verb on Codex and Claude Code: reported NO-DATA by ruling, never a pass, never a block. GREEN
for such a host rests on three legs, each missing one NO-DATA: a SessionStart row; PreToolUse rows whose hook stdin
session_id is the session their own turn's stream started (_same_session: the Codex thread.started thread_id, the Claude
Code stream-json init session_id); and a SessionStart row
carrying the door run's transcript (door_turn), in that row's session, that exited 0 with usage and an agent answer
carrying a verdict word, after the door ROUTED: a host originated PreToolUse row of that session loaded a DOOR_SKILLS
SKILL.md under the installed plugin root and no hook refused it (the load record measured on 2026-10-04). The root is
the one --run measured, bound by plugin_install_sha256 to the candidate's door skills and read on disk at judging time
(a real folder, no link, named after the candidate's Codex manifest version, door skills byte equal to the candidate's).
On Claude Code the door transcript is the `claude --print --output-format stream-json` turn, answered in a result line
with is_error false, and the root is the one --plugin-dir it names, which must be the row's plugin_root and exactly
<candidate>/bundle (Claude Code installs nothing; the clean tree check holds the folder).
The evidence file must match <evidence>.sha256, which only the collector writes, checked last (it blocks GREEN, never
grants it).

KNOWN LIMITS (1.1.0, owner rule of 2026-10-04: no further review rounds on this gate):
- Owner machine trust: a run folder forged whole (rows, envelopes, manifest recomputed, and an install folder planted
  on disk with the candidate's door skills) cannot be told apart from a real run (spec section 13, question 4).
- The verdict word check adds little over the skill load row, which is what proves the door routed.
- Codex reads are accepted only as a Bash `cat`, `head` or `sed -n` of the exact path; a Codex read tool with a path
  field reads NO-DATA until its shape is measured.
- Claude Code door skill loads are accepted the same way; a load through the Skill tool (no shipped PreToolUse matcher,
  payload unmeasured) reads NO-DATA until measured.
- The throwaway home must survive until the cut: the install and the installed hook file are read on disk.
- The 2026-10-04 Codex run reads NO-DATA (the defective clock guard refused the skill load); GREEN needs a fresh run.
- When the evidence file is committed, <evidence>.sha256 must be committed beside it.

Starts no process: git objects are read through l0_scan's reader (loose objects and packs), so a checkout with no
readable git directory is NO-DATA, never a pass. The Claude Code stream crosscheck is HP1.e's (host_live_claude.py);
while that file is not in the tree the Claude Code host reads NO-DATA.

usage (repo root):
  python3 scripts/host_live_verify.py <evidence> --root <checkout>
Python 3.9 and 3.13, standard library only.
"""
import argparse
import base64
import binascii
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time
import zlib
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import host_live_proof as proof  # noqa: E402  the one parse_verdict, probe_of, payload_of and write_shim
import l0_scan  # noqa: E402  the one git object reader that starts no process

REQUIRED_HOSTS = ("claude", "codex", "antigravity")
REQUIRED_PROBES = ("door", "verb", "guarded_write")
EXTRA_PROBES = {"antigravity": ("post_invocation",)}   # the signed in proof of 5.3 needs a PostInvocation row
#: What a guarded write row shows on each host. "allow" (Antigravity, owner ruling 2026-09-30, F-007): the shipped adapter
#: answers json_allow and the host writes. "unguarded" (Claude Code and Codex, owner ruling 2026-10-04 hp1-guarded-write,
#: option A, the limit stated in docs/releases/1.1.0.md): no shipped hook guards the write, so every hook answers allow
#: (silent_exit_0 or json_allow) and the file is the host's own permission system's call, recorded, never credited to
#: Brother. "deny" (kept for the 1.1.1 guard): a deny with its reason, the target never written. Any other answer is RED.
EXPECTED_GUARDED_WRITE = {"claude": "unguarded", "codex": "unguarded", "antigravity": "allow"}
UNGUARDED_SOURCES = ("silent_exit_0", "json_allow")
#: Owner ruling 2026-10-04 hp1-codex-door (option A): Codex has no prompt submit hook and its SessionStart payload carries
#: no prompt (keys cwd, hook_event_name, model, permission_mode, session_id, source, transcript_path in the 2026-10-04
#: Codex run), so probe_of can never label a Codex door or verb row. Those probes read NO-DATA BY RULING: never a pass,
#: never a block, named in the verdict line. Codex owes three legs instead (module docstring): SessionStart, PreToolUse
#: cross-checked by session, and the door transcript showing an answer. The
#: prompt hook is a 1.1.1 unit; landing it is deleting this entry. A ruled probe row that does arrive is judged as usual.
#: Owner ruling 2026-10-05 hp1-claude-door (option A, "Same as Codex"): bundle/hooks/hooks.json registers no prompt
#: submit hook and the Claude Code SessionStart payload carries no prompt (keys cwd, hook_event_name, session_id,
#: source, transcript_path in the real 2.1.286 run of 2026-10-04), so Claude Code door and verb read NO-DATA by ruling
#: too and Claude Code owes the same three legs, read from its own shapes: the session is the session_id of the
#: stream-json init line, the door transcript is the `claude --print --output-format stream-json` turn of the door
#: sentence answered in a result line, and the door skills are read under the --plugin-dir the turn named.
RULED_NO_DATA = {"codex": ("door", "verb"), "claude": ("door", "verb")}
RULED_NO_DATA_REF = {"codex": "owner ruling 2026-10-04 hp1-codex-door, prompt hook planned for 1.1.1",
                     "claude": "owner ruling 2026-10-05 hp1-claude-door, prompt hook planned for 1.1.1"}
#: Owner decision 2026-10-03 (docs/decisions/scope-1.1.0-defer-to-1.1.1-2026-10-03.json): under 1.1.0 only the Claude
#: Code and Codex rows decide; every Antigravity row is reported NO-DATA (ASIDE_NOTE) and never decides. It is verify's
#: default and the CLI's (the live run of 2026-10-05 read "no row from antigravity" and nothing else under the old
#: every-host default); verify(..., required=REQUIRED_HOSTS) is the 1.1.1 bar.
DECIDING_HOSTS_1_1_0 = ("claude", "codex")
ASIDE_NOTE = "%s: NO-DATA: experimental, certification moved to 1.1.1 by owner decision 2026-10-03, %d row(s) not judged"

GREEN, RED, NO_DATA = 0, 1, 3
SCHEMA = "hp1.v1"
PLUGIN_PATHS = ("plugin", "bundle", "products")
WITNESS_REL = "scripts/host_hook_trace.py"
TRANSLATOR_REL = "scripts/codex_hooks_install.py"
TOOL_SCRIPTS = (WITNESS_REL, "scripts/host_live_proof.py", "scripts/host_live_verify.py", "scripts/host_live_claude.py",
                TRANSLATOR_REL)
CODEX_MANIFEST_REL = "bundle/.codex-plugin/plugin.json"
SHIPPED_HOOKS_REL = {"antigravity": "bundle/.antigravity-plugin/hooks.json", "claude": "bundle/hooks/hooks.json"}
FUTURE_SLACK_S = 300       # a recorded_at later than now by more than this is a forged or broken clock
SHELLS = ("sh", "bash", "zsh", "dash", "ksh")
IGNORED_NAMES = ("__pycache__", ".DS_Store")   # the root .gitignore ignores both at every depth
DENY_SOURCES = ("exit_2", "json_deny")
KNOWN_VERDICTS = ("allow", "deny", "ask", "no_data")
DELIVERED_PROBES = REQUIRED_PROBES + ("post_invocation",)
KNOWN_PROBES = DELIVERED_PROBES + ("other",)
TOOL_CALL_TYPES = ("tool_use", "command_execution", "function_call", "local_shell_call", "mcp_tool_call")
EXTRA_HOST = re.compile(r"^[a-z][a-z0-9-]{1,31}$")   # a named fourth host: ignored and reported
LOADER_KEYS = ("_evidence_dir", "_line")            # set by load_rows only; a row line carrying one is refused
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$")
HP1E_MISSING = ("scripts/host_live_claude.py (HP1.e) is not in the tree, so the Claude Code stream cannot be re-parsed "
                "against the shim rows")
OWNER_COMMAND = ("HP1_LIVE=1 python3 scripts/host_live_proof.py --run <host> with the owner steps of "
                 "docs/how-to/host-live-proof.md")

#: Every evidence row carries these, with these types; write_row adds the raw_* keys and truncated.
REQUIRED_KEYS = (
    ("schema", str), ("host", str), ("run_id", str), ("probe", str), ("event", str), ("verdict", str),
    ("verdict_source", str), ("exit_code", int), ("stderr_len", int), ("hook_command", list), ("script", str),
    ("script_sha256", str), ("plugin_root", str), ("raw_in_path", str), ("raw_in_sha256", str), ("raw_in_len", int),
    ("raw_out_path", str), ("raw_out_sha256", str), ("raw_out_len", int), ("truncated", bool), ("parent_chain", list),
    ("host_roots", list), ("host_bin_realpath", str), ("host_version", str), ("recorded_at", str),
    ("plugin_tree_sha256", str), ("tree_clean", bool), ("shim_dir", str), ("shim_path", str), ("shim_sha256", str),
    ("real_python", str), ("witness_argv", list), ("witness_sha256", str), ("hooks_path", str), ("hooks_sha256", str),
    ("hooks_expected_sha256", str), ("tool_sha256", dict),
)


class VerifyRefused(ValueError):
    """Hostile or wrong typed input to a public function: refused, never judged."""


class VerifyNoData(LookupError):
    """A fact the verdict needs cannot be read (no git directory, no plugin history, a gone file): NO-DATA."""


# -- small guards ---------------------------------------------------------------------------------------------------

def _need_text(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise VerifyRefused("%s must be a non empty string, got %s" % (name, type(value).__name__))
    return value


def _need_now(now):
    if isinstance(now, bool) or not isinstance(now, int) or not 0 <= now < 2 ** 62:
        raise VerifyRefused("now must be an integer count of seconds, got %s" % type(now).__name__)
    return now


def _need_row(row):
    if not isinstance(row, dict) or not all(isinstance(k, str) for k in row):
        raise VerifyRefused("row must be a dict with string keys, got %s" % type(row).__name__)
    return row


def _need_rows(rows):
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise VerifyRefused("rows must be a list of dicts, got %s" % type(rows).__name__)
    return rows


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_sha256(value):
    return isinstance(value, str) and SHA256.match(value) is not None


def _bare_name(value):
    """A file name beside the evidence file: never a path, never a dot name."""
    return isinstance(value, str) and value not in ("", ".", "..") and os.path.basename(value) == value \
        and "\x00" not in value and "\\" not in value


def _show(value):
    return repr(value)[:60]


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _stamp(text):
    """Seconds since the epoch for an ISO UTC stamp as the witness writes it, or None."""
    m = STAMP.match(text) if isinstance(text, str) else None
    if not m:
        return None
    try:
        parts = [int(x) for x in m.groups()[:6]]
        when = datetime.datetime(*parts, tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return when.timestamp() + (int((m.group(7) or "0").ljust(6, "0")) / 1e6)


def _iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# -- the evidence file ----------------------------------------------------------------------------------------------

def _no_constant(name):
    raise ValueError("%s is not JSON" % name)


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key %s" % key)
        out[key] = value
    return out


def _json(data):
    return json.loads(data.decode("utf-8"), parse_constant=_no_constant, object_pairs_hook=_no_duplicates)


def _summary_shape(doc):
    return isinstance(doc, dict) and "outcome" in doc and "events_fired" in doc


def load_rows(path: str) -> Tuple[List[Dict[str, object]], str]:
    """(rows, problem) for the evidence file at path. problem is "" when every line is a row; otherwise it starts with
    NO-DATA (no file, an unreadable file, an empty file or one of blank lines only) or RED (a line that is not a JSON
    object, a truncated last line, the old summary shape {outcome, events_fired}, a line carrying a key only this
    reader sets) and rows is empty. Each row comes back with _evidence_dir (the folder its raw bytes and envelopes sit
    in) and _line. A line repeated byte for byte is one row: the same record appended twice is counted once."""
    _need_text(path, "path")
    try:
        data = _read(path)
    except FileNotFoundError:
        return [], "NO-DATA: %s is not recorded yet; the owner run writes it: %s" % (path, OWNER_COMMAND)
    except OSError as exc:
        return [], "NO-DATA: %s is unreadable: %s" % (path, exc)
    if not data.strip():
        return [], "NO-DATA: %s holds no row: no host has recorded a hook fire" % path
    try:
        whole = _json(data)
    except (UnicodeDecodeError, ValueError, RecursionError):
        whole = None
    if _summary_shape(whole):
        return [], ("RED: %s is the old summary shape {outcome, events_fired} with no rows: a summary is not "
                    "evidence of a host fire" % path)
    lines = data.split(b"\n")
    if lines[-1] != b"":
        return [], "RED: %s line %d is truncated (no line end): a cut row is refused, never dropped" % (path, len(lines))
    folder = os.path.dirname(os.path.abspath(path))
    rows, seen = [], set()
    for n, line in enumerate(lines[:-1], 1):
        if not line.strip():
            continue
        try:
            doc = _json(line)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return [], "RED: %s line %d is not JSON" % (path, n)
        if not isinstance(doc, dict):
            return [], "RED: %s line %d is not a JSON object" % (path, n)
        if _summary_shape(doc):
            return [], "RED: %s line %d is the old summary shape {outcome, events_fired}, not a row" % (path, n)
        if any(key in doc for key in LOADER_KEYS):
            return [], "RED: %s line %d carries %s, a key only this reader sets" % (path, n, "/".join(LOADER_KEYS))
        if line in seen:
            continue
        seen.add(line)
        doc.update({"_evidence_dir": folder, "_line": n})
        rows.append(doc)
    return rows, ""


def _shape(row):
    """'' when the row carries every required key with its type, else what is wrong."""
    for key, kind in REQUIRED_KEYS:
        if key not in row:
            return "the row has no %s" % key
        value = row[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            return "%s is a %s, not a %s" % (key, type(value).__name__, kind.__name__)
    if row["schema"] != SCHEMA:
        return "schema %s is not %s (a trace row is not an evidence row)" % (_show(row["schema"]), SCHEMA)
    if _stamp(row["recorded_at"]) is None:
        return "recorded_at %s is not an ISO UTC stamp" % _show(row["recorded_at"])
    if not row["run_id"].strip():
        return "run_id is empty"
    if not row["hook_command"] or not all(isinstance(a, str) for a in row["hook_command"]) \
            or os.path.basename(row["hook_command"][0]) != os.path.basename(row["script"]):
        return "hook_command %s does not name the script %s" % (_show(row["hook_command"]), _show(row["script"]))
    return ""


def _raw(row, name):
    """(bytes, "") for raw_in or raw_out beside the evidence, re-hashed; (None, why) when missing or altered."""
    rel, digest, folder = row.get(name + "_path"), row.get(name + "_sha256"), row.get("_evidence_dir")
    if not _bare_name(rel) or not _is_sha256(digest) or not isinstance(folder, str):
        return None, "%s_path %s is not a file name beside the evidence with a sha256" % (name, _show(rel))
    try:
        data = _read(os.path.join(folder, rel))
    except OSError:
        return None, "the %s file %s is missing beside the evidence" % (name, rel)
    if _sha256(data) != digest:
        return None, "the %s file %s does not hash to %s_sha256: the raw bytes were altered" % (name, rel, name)
    length = row.get(name + "_len")
    if not _is_int(length) or (length != len(data) and row.get("truncated") is False):
        return None, "the %s file %s holds %d bytes, not the %s recorded" % (name, rel, len(data), _show(length))
    return data, ""


# -- host origin ----------------------------------------------------------------------------------------------------

def _roots(value):
    """The host roots as normalised absolute paths, or [] when the list is missing or any entry is malformed."""
    if not isinstance(value, list) or not value:
        return []
    out = []
    for root in value:
        if not isinstance(root, str) or not os.path.isabs(root) or "\x00" in root:
            return []
        norm = os.path.normpath(root)
        if norm == os.sep or len([p for p in norm.split(os.sep) if p]) < 2:
            return []   # "/" or a top level folder would make every process a host
        out.append(norm)
    return out


def _chain(value):
    """The parent chain as a list of {pid, ppid, comm, exe, argv}, or [] when any link is malformed."""
    if not isinstance(value, list):
        return []
    for link in value:
        if not isinstance(link, dict) or not _is_int(link.get("pid")) or not _is_int(link.get("ppid")) \
                or not all(isinstance(link.get(k), str) for k in ("comm", "exe", "argv")):
            return []
    return value


def _under(path, roots):
    if not isinstance(path, str) or not os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    return any(norm == root or norm.startswith(root + os.sep) for root in roots)


def host_ancestor(row: Dict[str, object]) -> bool:
    """True when an ANCESTOR of the hook process (the parent_chain after its first link, the witness itself) is an
    executable under one of the row's host_roots: recomputed from parent_chain and host_roots, a stored host_ancestor
    value is never read. A malformed chain or root list is False (not host originated), never a guess."""
    _need_row(row)
    roots, chain = _roots(row.get("host_roots")), _chain(row.get("parent_chain"))
    if not roots or len(chain) < 2:
        return False
    return any(_under(link["exe"], roots) for link in chain[1:])


def _is_witness(link, witness_argv):
    if not isinstance(witness_argv, list) or not witness_argv or not all(isinstance(a, str) and a for a in witness_argv):
        return False
    if os.path.basename(witness_argv[0]) != os.path.basename(WITNESS_REL):
        return False
    return link["argv"].endswith(" " + " ".join(witness_argv))


def _is_shim(link, shim_dir):
    shim = os.path.join(shim_dir, "python3")
    return link["exe"] == shim or shim in link["argv"].split(" ")[:2]


def _is_shell(link):
    names = (os.path.basename(link["exe"]), os.path.basename(link["argv"].split(" ")[0]), link["comm"])
    return any(name.lstrip("-") in SHELLS for name in names)


def _hook_commands(path):
    """Every command string the installed hook file at path defines; an empty set when it cannot be read."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return set()
    try:
        doc = _json(_read(path))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return set()
    found, stack = set(), [doc]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key == "command" and isinstance(value, str):
                    found.add(value)
                else:
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _runs_shipped_command(argv, row):
    """True when argv is exactly `<shell> -c <command>` for a command the row's installed hooks.json defines (as written,
    or with ${CLAUDE_PLUGIN_ROOT} expanded to the row's plugin_root, the host's own substitution)."""
    head, sep, command = argv.partition(" -c ")
    if not sep or " " in head or os.path.basename(head).lstrip("-") not in SHELLS:
        return False
    commands = _hook_commands(row.get("hooks_path"))
    root = row.get("plugin_root")
    expanded = {c.replace(proof.PLUGIN_ROOT_CITATION, root) for c in commands} if isinstance(root, str) else set()
    return command in commands or command in expanded


def host_launched(row: Dict[str, object], shim_dir: str) -> bool:
    """True when the launch chain is one a host produces. Walking up from the hook process the allowed links are, in
    order: the witness's own interpreter (its argv ends with the row's witness_argv), the driver's shim sh (the shim
    file in shim_dir), at most one shell the host's own launcher started (its parent under a host root AND its argv
    exactly `sh -c <the shipped command>` as the row's installed hooks.json defines it), and then a host root executable.
    Any other process before the first host root executable (a terminal tool, a second shell, a wrapper script, a python3
    that is not the witness) means the model, not the host's hook launcher, started the hook: False."""
    _need_row(row)
    if not isinstance(shim_dir, str) or not os.path.isabs(shim_dir) or "\x00" in shim_dir:
        raise VerifyRefused("shim_dir must be an absolute path, got %s" % type(shim_dir).__name__)
    roots, chain = _roots(row.get("host_roots")), _chain(row.get("parent_chain"))
    if not roots or len(chain) < 2:
        return False
    if any(link["ppid"] != parent["pid"] for link, parent in zip(chain, chain[1:])):
        return False   # a gapped chain is not a launch chain
    if not _is_witness(chain[0], row.get("witness_argv")):
        return False
    at = 1
    if _is_shim(chain[at], shim_dir):
        at += 1
    if at < len(chain) and _is_shell(chain[at]):
        parent = chain[at + 1] if at + 1 < len(chain) else None
        if parent is None or _is_shell(parent) or not _under(parent["exe"], roots):
            return False
        if not _runs_shipped_command(chain[at]["argv"], row):
            return False
        at += 1
    return at < len(chain) and _under(chain[at]["exe"], roots)


# -- signed in ------------------------------------------------------------------------------------------------------

def _envelope(row, key):
    """(argv, exit_code, stdout, "") for the envelope a row names under key, re-hashed from its bytes; else why not."""
    ref, folder = row.get(key), row.get("_evidence_dir")
    if not isinstance(ref, dict):
        return None, None, b"", "no %s envelope" % key
    name, digest, argv = ref.get("envelope_path"), ref.get("envelope_sha256"), ref.get("argv")
    if not _bare_name(name) or not _is_sha256(digest) or not isinstance(argv, list) \
            or not argv or not all(isinstance(a, str) for a in argv) or not isinstance(folder, str):
        return None, None, b"", "the %s envelope reference is malformed" % key
    try:
        data = _read(os.path.join(folder, name))
    except OSError:
        return None, None, b"", "the %s envelope %s is missing beside the evidence" % (key, name)
    if _sha256(data) != digest:
        return None, None, b"", "the %s envelope %s does not hash to its envelope_sha256" % (key, name)
    try:
        doc = _json(data)
        out = base64.b64decode(doc["stdout_b64"], validate=True) if isinstance(doc, dict) \
            and isinstance(doc.get("stdout_b64"), str) else None
    except (UnicodeDecodeError, ValueError, RecursionError, binascii.Error):
        doc, out = None, None
    if out is None or doc.get("argv") != argv or not _is_int(doc.get("exit_code")):
        return None, None, b"", "the %s envelope %s is not {argv, exit_code, stdout_b64} for its argv" % (key, name)
    return argv, doc["exit_code"], out, ""


def _json_lines(data):
    for line in data.split(b"\n"):
        try:
            doc = _json(line) if line.strip() else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            doc = None
        if doc is not None:
            yield doc


def _usage_seen(stdout):
    """True when some stdout line is a JSON object with a usage key: re-derived from the bytes, never a stored flag."""
    return any(isinstance(doc, dict) and "usage" in doc for doc in _json_lines(stdout))


def _tool_call_names(stdout, names):
    """The first of names a model tool call in stdout carries, or ''."""
    stack = list(_json_lines(stdout))
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("type") in TOOL_CALL_TYPES:
                text = json.dumps(item, sort_keys=True)
                hit = next((n for n in names if n and n in text), "")
                if hit:
                    return hit
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return ""


def _turn_reason(host, row, scripts):
    argv, code, out, why = _envelope(row, "model_turn")
    if why:
        return "line %s: %s" % (row.get("_line"), why)
    if host == "codex" and not ("exec" in argv and "--json" in argv):
        return "line %s: the model turn is not a `codex exec --json` turn" % row.get("_line")
    if host == "claude" and "stream-json" not in argv:
        return "line %s: the model turn is not a stream-json Claude Code turn" % row.get("_line")
    if code != 0:
        return "line %s: the model turn exited %d (a failed model turn)" % (row.get("_line"), code)
    if not _usage_seen(out):
        return "line %s: the model turn's stdout holds no JSON line with a usage key" % row.get("_line")
    named = _tool_call_names(out, scripts)
    if named:
        return "line %s: a model tool call names the hook script %s, so the model could have forged the row" % (
            row.get("_line"), named)
    return ""


#: The skills the door routes to on Codex: the shared door and the status verb it was asked for. Measured in the
#: 2026-10-04 Codex run: the door thread's PreToolUse hook stdin carried `cat <CODEX_HOME>/plugins/cache/brother/
#: brother/1.1.0/skills/brothermode-status/SKILL.md`, while the `codex exec --json` stream logged no tool item at all.
#: Since 1.1.1 (U1, owner ruling 2026-10-10) the status verb ships as brother-status; brothermode-status routes
#: to it through the door, so a load of the verb skill is what proves the route.
DOOR_SKILLS = ("using-brother", "brother-status")
#: The status skill prints no fixed heading; the one shape it fixes is that the answer keeps PASS, FAIL and NO-DATA
#: distinct, so a routed answer carries one of them as a word (the 2026-10-04 door answer read "**NO-DATA**"). This
#: adds little over the skill load row, which is what proves the door routed; it is kept as a cheap second filter.
VERDICT_WORD = re.compile(r"(?<![A-Za-z-])(?:PASS|FAIL|NO-DATA)(?![A-Za-z-])")


def _agent_answered(stdout, host="codex"):
    """True when the turn's answer carries a verdict word, the status verb's answer shape: re-derived from the bytes. A
    reply with no verdict word did not route. Codex: some `codex exec --json` line completes an agent_message item with
    that text. Claude Code: some stream-json {"type": "result"} line with is_error exactly false carries it in result
    (the shape measured on 2026-10-04, where the not signed in sessions wrote is_error true)."""
    if host == "claude":
        return any(isinstance(doc, dict) and doc.get("type") == "result" and doc.get("is_error") is False
                   and isinstance(doc.get("result"), str) and VERDICT_WORD.search(doc["result"])
                   for doc in _json_lines(stdout))
    for doc in _json_lines(stdout):
        item = doc.get("item") if isinstance(doc, dict) and doc.get("type") == "item.completed" else None
        if isinstance(item, dict) and item.get("type") == "agent_message" \
                and isinstance(item.get("text"), str) and VERDICT_WORD.search(item["text"]):
            return True
    return False


def _read_target(payload):
    """The one file path a Bash shaped tool call reads with a named read verb, or None. Run 3 recorded only Bash tool
    calls on Codex ({"command": "cat <path>"}); no separate read tool with a path field appeared, so none is accepted.
    The command is split with shlex and must be exactly one of: `cat PATH`, `head PATH`, `head -n N PATH`, `head -N PATH`,
    `sed -n 'A,Bp' PATH` (or 'Ap'). Anything else (another verb, a pipe, a second command, a redirect) is None."""
    call = payload.get("tool_input")
    command = call.get("command") if isinstance(call, dict) else None
    try:
        words = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return None
    if len(words) == 2 and words[0] in ("cat", "head"):
        return words[1]
    if len(words) == 3 and words[0] == "head" and re.match(r"^-\d+$", words[1]):
        return words[2]
    if len(words) == 4 and words[0] == "head" and words[1] == "-n" and words[2].isdigit():
        return words[3]
    if len(words) == 4 and words[0] == "sed" and words[1] == "-n" and re.match(r"^\d+(,\d+)?p$", words[2]):
        return words[3]
    return None


def _install_root(start, facts):
    """The installed plugin root the run recorded (plugin_install, measured by --run after the install), or None when it
    is absent, not exactly <CODEX_HOME>/plugins/cache/brother/brother/<version> (CODEX_HOME being the folder of the
    hook file the row read), or not bound to disk (a non normal root, `..`, `.`, `//` or a trailing slash, fails the
    folder test, the digest or the disk read below): plugin_install_sha256, which --run computed from the installed door skills, must equal install_digest of
    this root over the candidate's committed bundle/skills/<name>/SKILL.md blobs. A row edited to name another root
    (.../9.9.9), or an install whose door skills are not the candidate's, never matches."""
    root = start.get("plugin_install")
    if not isinstance(root, str):
        return None
    cache = os.path.join(os.path.dirname(start["hooks_path"]), "plugins", "cache", "brother", "brother")
    if os.path.dirname(root) != cache:
        return None
    shas = dict((name, facts.blob("bundle/skills/%s/SKILL.md" % name)) for name in DOOR_SKILLS)
    if None in shas.values() or start.get("plugin_install_sha256") != proof.install_digest(root, shas):
        return None
    return root if _install_on_disk(root, shas, facts) else None


def _claude_root(row, argv, facts):
    """The plugin folder a Claude Code door turn loaded, or None: the one --plugin-dir its argv names, which must be the
    plugin_root the witness recorded on the row and exactly <candidate>/bundle, the folder judged here. Claude Code
    installs nothing: that folder is the install, verified on disk by the clean tree check (facts.dirty reads
    bundle/skills with the rest), and it must hold the candidate's door skills."""
    root = argv[argv.index("--plugin-dir") + 1] if argv.count("--plugin-dir") == 1 and \
        argv.index("--plugin-dir") + 1 < len(argv) else None
    if root is None or root != row.get("plugin_root") or root != os.path.join(facts.root, "bundle"):
        return None
    return root if all(facts.blob("bundle/skills/%s/SKILL.md" % name) for name in DOOR_SKILLS) else None


def _install_on_disk(root, shas, facts):
    """True when the install is on disk as the candidate's: root is a real folder reached through no link, named after
    the version the candidate's Codex manifest declares, and every door skill under it is a regular file, reached
    through no link, whose bytes hash to the candidate's committed blob. Read now, at judging time, like the installed
    hook file (_bytes_ran): a row naming a root nobody installed reads NO-DATA, never GREEN."""
    if os.path.basename(root) != facts.plugin_version():
        return False   # a missing root or a link on its path fails each skill's read or realpath below
    for name, want in shas.items():
        path = os.path.join(root, "skills", name, "SKILL.md")
        try:
            if os.path.realpath(path) != path or _sha256(_read(path)) != want:
                return False
        except OSError:
            return False
    return True


def _loads_door_skill(row, root):
    """True when a PreToolUse row's host written stdin reads, with a named read verb, exactly
    <root>/skills/<one of DOOR_SKILLS>/SKILL.md: one whole path token, no `..` component, equal after normalisation."""
    path = _read_target(proof.payload_of(_raw(row, "raw_in")[0]) or {})   # _integrity already read these bytes
    if path is None or ".." in path.split("/"):
        return False
    return os.path.normpath(path) in [os.path.join(root, "skills", name, "SKILL.md") for name in DOOR_SKILLS]


def _thread_of(row, key):
    """The session the row's `key` envelope started, or None (an unreadable envelope reads as no stdout, so no line).
    Codex: the thread_id of the first thread.started line. Claude Code: the session_id of the first stream-json
    {"type": "system", "subtype": "init"} line, which in the real 2.1.286 run equals the session_id of that session's
    hook stdin."""
    claude = row.get("host") == "claude"
    for doc in _json_lines(_envelope(row, key)[2]):
        if not isinstance(doc, dict):
            continue
        if claude and doc.get("type") == "system" and doc.get("subtype") == "init":
            return doc.get("session_id")
        if not claude and doc.get("type") == "thread.started":
            return doc.get("thread_id")
    return None


def _same_session(row: Dict[str, object], key: str) -> bool:
    """The Codex cross-check (Claude Code's is _crosscheck over the host's hook event stream, which Codex does not
    emit): the session_id in the hook stdin the host wrote equals the thread_id the host's own `codex exec --json`
    stream of the `key` envelope started. Measured equal on every SessionStart and PreToolUse row of the 2026-10-04
    Codex run. Re-derived from both byte sources, never a stored flag."""
    sid = _session_id(row)
    return sid is not None and sid == _thread_of(row, key)


def _session_id(row):
    """The non empty session_id string the host wrote into the row's hook stdin, or None."""
    raw_in, why = _raw(row, "raw_in")
    payload = None if why else proof.payload_of(raw_in)
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    return sid if isinstance(sid, str) and sid else None


def _door_answered(row, tools, facts):
    """'' when the row's door_turn envelope (owner ruling 2026-10-04 hp1-codex-door) is this run's `codex exec --json`
    turn of the door sentence, exited 0 with a usage line and an agent answer carrying a verdict word, in the row's
    session, and the door ROUTED: some PreToolUse row of tools (this run's other labelled ones) in that same session
    loads a door skill under the installed plugin root and no hook refused that load; else why not."""
    ref = row.get("door_turn")
    if isinstance(ref, dict) and ref.get("envelope_path") != row["run_id"] + "-door.json":
        return "line %s: the door_turn envelope %s is not this run's door transcript" % (
            row.get("_line"), _show(ref.get("envelope_path")))
    argv, code, out, why = _envelope(row, "door_turn")
    if why:
        return "line %s: %s" % (row.get("_line"), why)
    claude = row.get("host") == "claude"
    if claude and (not ("--print" in argv and "stream-json" in argv) or argv[-1] != proof.DOOR_SENTENCE):
        return "line %s: the door_turn is not a `claude --print --output-format stream-json` turn of the door " \
               "sentence" % row.get("_line")
    if not claude and (not ("exec" in argv and "--json" in argv) or argv[-1] != proof.DOOR_SENTENCE):
        return "line %s: the door_turn is not a `codex exec --json` turn of the door sentence" % row.get("_line")
    if code != 0:
        return "line %s: the door turn exited %d" % (row.get("_line"), code)
    if not _usage_seen(out):
        return "line %s: the door turn's stdout holds no JSON line with a usage key" % row.get("_line")
    if not _agent_answered(out, row.get("host")):
        return "line %s: the door turn holds no agent answer carrying PASS, FAIL or NO-DATA: the door did not " \
               "route to the status verb" % row.get("_line")
    if not _same_session(row, "door_turn"):
        return "line %s: the SessionStart row's session_id is not the door turn's thread_id" % row.get("_line")
    root = _claude_root(row, argv, facts) if claude else _install_root(row, facts)
    if root is None:
        return "line %s: the run recorded no installed plugin root (%s) bound to the candidate's door skills and " \
               "present on disk as the candidate's install" % (row.get("_line"), "the door turn's --plugin-dir, the "
                                                               "row's plugin_root" if claude else "plugin_install")
    loads = [t for t in tools if _loads_door_skill(t, root) and _session_id(t) == _session_id(row)]
    if not loads:
        return "line %s: no PreToolUse row in the door's session loads %s under the installed plugin root: the door " \
               "did not route" % (row.get("_line"), " or ".join(DOOR_SKILLS))
    if any(t["verdict"] != "allow" for t in loads):
        return "line %s: a hook refused the door skill load (line %s)" % (
            row.get("_line"), next(t["_line"] for t in loads if t["verdict"] != "allow"))
    return ""


def _login_reason(row):
    argv, code, _out, why = _envelope(row, "signed_in")
    if why:
        return "line %s: %s" % (row.get("_line"), why)
    if not any(a == "login" and b == "status" for a, b in zip(argv, argv[1:])):
        return "line %s: the signed_in envelope is not `codex login status`" % row.get("_line")
    if code != 0:
        return "line %s: `codex login status` exited %d" % (row.get("_line"), code)
    return ""


def signed_in_reason(host: str, rows: List[Dict[str, object]]) -> str:
    """'' when the host had a live account session, else why not (RED, not signed in). Re-derived from envelope bytes
    or raw hook stdin, never from a stored flag. Codex: every row's `codex login status` envelope exits 0 and its
    `codex exec --json` model turn exits 0 with a usage line. Claude Code: every row's stream-json model turn exits 0
    with a usage line. Both: no model tool call names a hook script. Antigravity (no headless probe, no envelope): a
    PostInvocation row whose host written stdin carries invocationNum, an integer of at least 1, from a host ancestor.
    Signed in is a live session, never a cryptographic signature."""
    if not isinstance(host, str) or host not in REQUIRED_HOSTS:
        raise VerifyRefused("host must be one of %s, got %s" % (", ".join(REQUIRED_HOSTS), _show(host)))
    _need_rows(rows)
    if not rows:
        return "no row to show a session"
    if host == "antigravity":
        for row in rows:
            raw_in, why = _raw(row, "raw_in")
            event = row.get("event")
            if why or not isinstance(event, str) or proof.probe_of(raw_in, event=event) != "post_invocation":
                continue
            num = proof.payload_of(raw_in).get("invocationNum")
            if _is_int(num) and num >= 1 and host_ancestor(row):
                return ""
        return "no PostInvocation row whose host written stdin carries invocationNum of at least 1 from a host ancestor"
    scripts = sorted({os.path.basename(r["script"]) for r in rows if isinstance(r.get("script"), str)})
    for row in rows:
        why = _turn_reason(host, row, scripts) or (_login_reason(row) if host == "codex" else "")
        if why:
            return why
    return ""


# -- the guarded write ----------------------------------------------------------------------------------------------

def _deny_explained(row):
    """A deny carries the hook's stderr or a reason: an exit 2 with empty stderr and empty stdout is a crash."""
    if _is_int(row.get("stderr_len")) and row["stderr_len"] > 0:
        return True
    raw_out, why = _raw(row, "raw_out")
    doc = proof.payload_of(raw_out) if not why else None
    if not doc:
        return False
    specific = doc.get("hookSpecificOutput")
    reasons = [doc.get("reason"), specific.get("permissionDecisionReason") if isinstance(specific, dict) else None]
    return any(isinstance(r, str) and r.strip() for r in reasons)


def effect_matches(row: Dict[str, object]) -> bool:
    """True when a guarded_write row shows what EXPECTED_GUARDED_WRITE says its host does. Claude Code and Codex
    (unguarded): an allow from silent_exit_0 or json_allow with the target absent before; whether it exists after is the
    host's own permission system, recorded and not judged. Antigravity (allow): an allow from json_allow (the shipped
    adapter's answer) with the target absent before and written after. A host set to deny: a deny from exit_2 or
    json_deny with stderr or a reason, the target absent before and after. Anything else, an unknown host or a missing
    effect record included, is False."""
    _need_row(row)
    host = row.get("host")
    expected = EXPECTED_GUARDED_WRITE.get(host) if isinstance(host, str) else None
    effect = row.get("effect")
    if expected is None or not isinstance(effect, dict):
        return False
    target = effect.get("path")
    if not isinstance(target, str) or os.path.basename(target) != proof.GUARDED_WRITE_NAME:
        return False
    before, after = effect.get("exists_before"), effect.get("exists_after")
    if before is not False or not isinstance(after, bool):
        return False   # the target was not clean before the probe, or the record says nothing
    verdict, source = row.get("verdict"), row.get("verdict_source")
    if expected == "unguarded":   # Claude Code and Codex, owner ruling 2026-10-04: no shipped guard, the host decides
        return verdict == "allow" and source in UNGUARDED_SOURCES
    if expected == "allow":   # Antigravity, owner ruling 2026-09-30: the hook answers allow and the host writes
        return verdict == "allow" and source == "json_allow" and after is True
    return verdict == "deny" and source in DENY_SOURCES and after is False and _deny_explained(row)


def _need_required(required):
    """The deciding hosts as a tuple in REQUIRED_HOSTS order: distinct names from REQUIRED_HOSTS, at least one."""
    if isinstance(required, (str, bytes)) or not isinstance(required, (tuple, list)) or not required \
            or len(set(required)) != len(required) or any(h not in REQUIRED_HOSTS for h in required):
        raise VerifyRefused("required must name distinct hosts from %s, got %s" % (", ".join(REQUIRED_HOSTS), _show(required)))
    return tuple(host for host in REQUIRED_HOSTS if host in required)


def missing_hosts(rows: List[Dict[str, object]], required=REQUIRED_HOSTS) -> List[str]:
    """The deciding hosts (required, default every host) no row names, in REQUIRED_HOSTS order."""
    _need_rows(rows)
    required = _need_required(required)
    present = [row.get("host") for row in rows]
    return [host for host in required if host not in present]


# -- the candidate, read from git without a process ------------------------------------------------------------------

def _open(root):
    """(store, tree) of HEAD at the checkout root; VerifyNoData when there is no readable git directory."""
    _need_text(root, "root")
    located = l0_scan._git_dirs(root)
    if located is None:
        raise VerifyNoData("%s has no readable git directory (git absent or not a checkout)" % root)
    git_dir, common = located
    head = l0_scan._resolve_ref(git_dir, common, "HEAD")
    if head is None:
        raise VerifyNoData("HEAD does not resolve in %s" % root)
    store = l0_scan._ObjectStore(common)
    return store, store.commit(head)[0], head


def _reading(fn, *args):
    """fn(*args), with every way the object store can fail turned into NO-DATA."""
    try:
        return fn(*args)
    except (l0_scan.ScanRefused, OSError, ValueError, IndexError, KeyError, zlib.error, RecursionError) as exc:
        if isinstance(exc, VerifyRefused):
            raise
        raise VerifyNoData("the git objects cannot be read: %s" % exc)


def _walk(store, tree, prefix):
    """(path, mode, sha) of every non tree entry below tree, in git's tree order."""
    for mode, name, sha in store.tree_entries(tree):
        path = prefix + name
        if mode in ("40000", "040000"):
            for item in _walk(store, sha, path + "/"):
                yield item
        else:
            yield path, mode, sha


def _plugin_entries(store, tree):
    for mode, name, sha in store.tree_entries(tree):
        if name not in PLUGIN_PATHS:
            continue
        if mode in ("40000", "040000"):
            for item in _walk(store, sha, name + "/"):
                yield item
        else:
            yield name, mode, sha


_C_ESCAPES = {7: b"a", 8: b"b", 9: b"t", 10: b"n", 11: b"v", 12: b"f", 13: b"r", 34: b'"', 92: b"\\"}


def _quoted(path):
    """The path as `git ls-tree` prints it with core.quotePath on: C style quoting when a byte needs it."""
    raw = path.encode("utf-8", "surrogateescape")
    if not any(c < 0x20 or c in (34, 92) or c >= 0x7f for c in raw):
        return raw
    out = bytearray(b'"')
    for c in raw:
        if c in _C_ESCAPES:
            out += b"\\" + _C_ESCAPES[c]
        elif c < 0x20 or c >= 0x7f:
            out += b"\\%03o" % c
        else:
            out.append(c)
    return bytes(out + b'"')


def _tree_listing(store, tree):
    lines = []
    for path, mode, sha in _plugin_entries(store, tree):
        kind = "commit" if mode == "160000" else "blob"
        lines.append(b"%06o %s %s\t%s\n" % (int(mode, 8), kind.encode(), sha.encode(), _quoted(path)))
    if not lines:
        raise VerifyNoData("HEAD holds nothing under plugin, bundle or products (git ls-tree prints nothing)")
    return b"".join(lines)


def tree_sha256(root: str) -> str:
    """sha256 of what `git ls-tree -r HEAD -- plugin bundle products` prints at the checkout root: the COMMITTED blobs,
    so an evidence file added later does not change it and an uncommitted edit cannot hide in it. VerifyNoData when
    there is no readable git directory or HEAD holds nothing under those paths."""
    _need_text(root, "root")
    return _reading(lambda: _sha256(_tree_listing(*_open(root)[:2])))


def _commit_info(store, sha, cache):
    if sha not in cache:
        kind, body = store.object(sha)
        if kind != "commit":
            raise VerifyNoData("object %s is a %s, not a commit" % (sha, kind))
        tree, parents, when = None, [], None
        for line in body.split(b"\n"):
            if not line:
                break
            if line.startswith(b"tree "):
                tree = line[5:].decode("ascii", "replace")
            elif line.startswith(b"parent "):
                parents.append(line[7:].decode("ascii", "replace"))
            elif line.startswith(b"committer "):
                stamp = line.rsplit(b" ", 2)[-2:-1]
                when = int(stamp[0]) if stamp and stamp[0].isdigit() else None
        if tree is None or when is None:
            raise VerifyNoData("commit %s carries no tree or no committer time" % sha)
        cache[sha] = (tree, parents, when)
    return cache[sha]


def _plugin_key(store, tree):
    found = dict((name, (mode, sha)) for mode, name, sha in store.tree_entries(tree) if name in PLUGIN_PATHS)
    return tuple(found.get(name) for name in PLUGIN_PATHS)


def _last_time(store, head):
    """The committer time of the newest commit touching plugin, bundle or products, walked as `git log -1 -- <paths>`
    simplifies history: a commit the same as one parent there follows that parent only."""
    cache, seen, cur = {}, set(), head
    while cur not in seen:
        seen.add(cur)
        tree, parents, when = _commit_info(store, cur, cache)
        mine = _plugin_key(store, tree)
        if not parents:
            if any(part is not None for part in mine):
                return when
            break
        same = next((p for p in parents if _plugin_key(store, _commit_info(store, p, cache)[0]) == mine), None)
        if same is None:
            return when
        cur = same
    raise VerifyNoData("no commit reachable from HEAD touches plugin, bundle or products (git log prints nothing)")


def last_plugin_commit_time(root: str) -> int:
    """The committer time (seconds since the epoch) of the last commit touching plugin, bundle or products at the
    checkout root, as `git log -1 --format=%ct -- plugin bundle products` prints it. VerifyNoData when there is no
    readable git directory or no such commit."""
    _need_text(root, "root")

    def read():
        store, _tree, head = _open(root)
        return _last_time(store, head)
    return _reading(read)


def _lookup(store, tree, rel):
    """(mode, sha) of the entry at rel in tree, or None."""
    entry, parts = None, rel.split("/")
    for i, part in enumerate(parts):
        entry = next(((m, s) for m, n, s in store.tree_entries(tree) if n == part), None)
        if entry is None:
            return None
        if i < len(parts) - 1:
            if entry[0] not in ("40000", "040000"):
                return None
            tree = entry[1]
    return entry


def _blob_bytes(store, tree, rel):
    entry = _lookup(store, tree, rel)
    if entry is None or entry[0] in ("40000", "040000", "160000"):
        return None
    kind, body = store.object(entry[1])
    if kind != "blob":
        raise VerifyNoData("%s is a %s, not a blob" % (rel, kind))
    return body


def _blob_sha256(store, tree, rel):
    body = _blob_bytes(store, tree, rel)
    return None if body is None else _sha256(body)


def _git_blob_sha1(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _changed(full, mode, sha):
    """Why the working file at full differs from the committed (mode, sha), or ''."""
    try:
        st = os.lstat(full)
    except OSError:
        return "deleted"
    if mode == "160000":
        return "" if stat.S_ISDIR(st.st_mode) else "type changed"
    if mode == "120000":
        if not stat.S_ISLNK(st.st_mode):
            return "type changed"
        data = os.fsencode(os.readlink(full))
    else:
        if not stat.S_ISREG(st.st_mode):
            return "type changed"
        if bool(st.st_mode & 0o100) != (mode == "100755"):
            return "mode changed"
        data = _read(full)
    return "" if _git_blob_sha1(data) == sha else "modified"


def _dirty(root, store, tree):
    """What `git status --porcelain -- plugin bundle products <tool scripts>` would list at root: tracked files changed
    or deleted, and untracked files (bytecode caches and .DS_Store aside, which the root .gitignore ignores)."""
    tracked = dict((path, (mode, sha)) for path, mode, sha in _plugin_entries(store, tree))
    for rel in TOOL_SCRIPTS:
        entry = _lookup(store, tree, rel)
        if entry is not None:
            tracked[rel] = entry
    out = []
    for rel in sorted(tracked):
        why = _changed(os.path.join(root, rel), *tracked[rel])
        if why:
            out.append("%s %s" % (rel, why))
    for top in PLUGIN_PATHS:
        for base, dirs, files in os.walk(os.path.join(root, top)):
            rel_base = os.path.relpath(base, root).replace(os.sep, "/")
            links = [d for d in dirs if os.path.islink(os.path.join(base, d))]
            dirs[:] = sorted(d for d in dirs if d not in links and d not in IGNORED_NAMES and rel_base + "/" + d not in tracked)
            for name in files + links:
                rel = rel_base + "/" + name
                if name not in IGNORED_NAMES and rel not in tracked:
                    out.append("%s untracked" % rel)
    for rel in TOOL_SCRIPTS:
        if rel not in tracked and os.path.lexists(os.path.join(root, rel)):
            out.append("%s untracked" % rel)
    return out


class _Candidate(object):
    """The candidate checkout's facts, read once: tree hash, last plugin commit time, dirt, committed blobs."""

    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.store, self.tree, head = _open(root)
        self.tree_sha256 = _sha256(_tree_listing(self.store, self.tree))
        self.last_commit = _last_time(self.store, head)
        self.dirty = _dirty(self.root, self.store, self.tree)
        self.tools = dict((rel, _blob_sha256(self.store, self.tree, rel) or "absent") for rel in TOOL_SCRIPTS)
        self._hooks = {}

    def blob(self, rel):
        return _reading(_blob_sha256, self.store, self.tree, rel)

    def plugin_version(self):
        """The version the candidate's Codex manifest declares: the folder name Codex installs it under (measured
        2026-10-04: the cache folder 1.1.0 matched bundle/.codex-plugin/plugin.json). None when unreadable."""
        data = _reading(_blob_bytes, self.store, self.tree, CODEX_MANIFEST_REL)
        try:
            doc = _json(data) if data is not None else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            doc = None
        version = doc.get("version") if isinstance(doc, dict) else None
        return version if isinstance(version, str) and version else None

    def rel(self, path):
        """path relative to the checkout when it lies inside it, else None."""
        if not isinstance(path, str) or not os.path.isabs(path):
            return None
        rel = os.path.relpath(os.path.normpath(path), self.root)
        return None if rel == os.curdir or rel.startswith(os.pardir) else rel.replace(os.sep, "/")

    def hooks_expected(self, host):
        if host not in self._hooks:
            self._hooks[host] = self._codex_hooks() if host == "codex" else self.blob(SHIPPED_HOOKS_REL[host])
        if self._hooks[host] is None:
            raise VerifyNoData("the candidate holds no %s hook file" % host)
        return self._hooks[host]

    def _codex_hooks(self):
        """sha256 of dump(build(products)) from the translator the installer ran, bound to the candidate's blob."""
        import codex_hooks_install as translator   # the one translation the Codex installer writes
        try:
            here = _sha256(_read(translator.__file__))
        except OSError as exc:
            raise VerifyNoData("the Codex translator cannot be read: %s" % exc)
        if here != self.blob(TRANSLATOR_REL):
            raise VerifyNoData("the verifier's %s is not the candidate's committed one" % TRANSLATOR_REL)
        built = translator.build([os.path.join(self.root, rel) for rel in translator.DEFAULT_PRODUCTS])
        if built["problems"]:
            raise VerifyNoData("the candidate's Codex hooks do not build: %s" % "; ".join(built["problems"])[:200])
        return _sha256(translator.dump(built["document"]).encode("utf-8"))


def candidate_facts(root: str) -> Dict[str, object]:
    """What the collector (host_live_proof.collect, HP1.f) binds a row to at recording time, read from the checkout
    root exactly as verify reads it back: plugin_tree_sha256 (tree_sha256), tree_clean (nothing differs under plugin,
    bundle, products or the tool scripts), dirty (what does), tool_sha256 (the committed blobs of TOOL_SCRIPTS,
    "absent" for one not committed) and hooks_expected_sha256 per host (None when the candidate holds no such hook
    file or its Codex translation does not build). VerifyNoData when there is no readable git directory or no plugin
    history; a hostile root is refused."""
    _need_text(root, "root")
    facts = _reading(_Candidate, os.path.abspath(root))
    expected = {}
    for host in REQUIRED_HOSTS:
        try:
            expected[host] = facts.hooks_expected(host)
        except VerifyNoData:
            expected[host] = None
    return {"plugin_tree_sha256": facts.tree_sha256, "tree_clean": not facts.dirty, "dirty": list(facts.dirty),
            "tool_sha256": dict(facts.tools), "hooks_expected_sha256": expected}


_SHIMS = {}


def _shim_sha256(real_python):
    """sha256 of the bytes host_live_proof.write_shim produces for real_python, written to a scratch folder."""
    if real_python not in _SHIMS:
        folder = tempfile.mkdtemp(prefix="hp1-verify-shim-")
        try:
            _SHIMS[real_python] = _sha256(_read(proof.write_shim(folder, real_python)))
        except proof.HP1Refused as exc:
            raise VerifyNoData("the recorded interpreter cannot be re-shimmed: %s" % exc)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return _SHIMS[real_python]


# -- the verdict ----------------------------------------------------------------------------------------------------

def _integrity(row):
    """The first way one row's own bytes contradict it, or ''."""
    raw_in, why = _raw(row, "raw_in")
    if why:
        return why
    raw_out, why = _raw(row, "raw_out")
    if why:
        return why
    if row["truncated"] is not False:
        return "the raw bytes are truncated, so the verdict cannot be re-derived"
    if proof.payload_of(raw_in) is None:
        return "raw_in is not a non empty JSON object"
    if row["verdict"] not in KNOWN_VERDICTS:
        return "unknown verdict word %s" % _show(row["verdict"])
    derived = proof.parse_verdict(raw_out, row["exit_code"])
    if (row["verdict"], row["verdict_source"]) != derived:
        return "verdict %s from %s is not what the raw stdout and exit code say (%s from %s)" % (
            row["verdict"], row["verdict_source"], derived[0], derived[1])
    if row["probe"] not in KNOWN_PROBES:
        return "unknown probe %s" % _show(row["probe"])
    label = proof.probe_of(raw_in, event=row["event"])
    if row["probe"] != label:
        return "probe %s is not what the host's raw stdin supports (%s): a relabelled row" % (row["probe"], label)
    return ""


def _origin(row):
    """The first RED condition one probe row shows about where it came from and what it answered, or ''."""
    version = row["host_version"].strip()
    if not version or version.lower() == "no_data":
        return "host_version %s is empty or no_data: the run pinned no host version" % _show(row["host_version"])
    if row["probe"] in ("door", "verb") and row["verdict"] in ("deny", "no_data"):
        return "a %s row whose verdict is %s: a hook that refuses everything never passes" % (row["probe"], row["verdict"])
    if row["tree_clean"] is not True:
        return "dirty: the row's tree_clean is false, so the tree was not clean when it was recorded"
    if not host_ancestor(row):
        return "not host originated: no ancestor of the hook runs under the host roots (a sandbox or harness run)"
    if not host_launched(row, row["shim_dir"]):
        return "not host launched: a process other than the host's hook launcher sits between the hook and the host"
    if not _under(row["host_bin_realpath"], _roots(row["host_roots"])):
        return "the host binary %s is not under the row's host roots" % row["host_bin_realpath"]
    if not _under(row["script"], _roots([row["plugin_root"]])):
        return "the hook script %s does not resolve under the row's plugin_root: not a shipped hook" % row["script"]
    if row["probe"] == "guarded_write" and not effect_matches(row):
        return "guarded write %s from %s with effect %s is not the %s EXPECTED_GUARDED_WRITE names for %s" % (
            row["verdict"], row["verdict_source"], _show(row.get("effect")),
            EXPECTED_GUARDED_WRITE[row["host"]], row["host"])
    return ""


def _binding(row, facts, now):
    """(code, why) for one probe row against the candidate: freshness first, then the committed bytes it ran."""
    stamp = _stamp(row["recorded_at"])
    if row["plugin_tree_sha256"] != facts.tree_sha256:
        return RED, "stale: plugin_tree_sha256 %s is not the candidate's %s" % (
            row["plugin_tree_sha256"][:12], facts.tree_sha256[:12])
    if stamp < facts.last_commit:
        return RED, "stale: recorded at %s, before the last commit to plugin, bundle or products (%s)" % (
            row["recorded_at"], _iso(facts.last_commit))
    if stamp > now + FUTURE_SLACK_S:
        return RED, "recorded at %s, more than %d s after now (%s): a future stamp cannot pass freshness" % (
            row["recorded_at"], FUTURE_SLACK_S, _iso(now))
    return 0, ""


def _bytes_ran(row, facts):
    """(code, why): the witness, hook script, tools, installed hook file and shim the row ran are the candidate's."""
    if row["witness_sha256"] != facts.blob(WITNESS_REL):
        return RED, "the witness that ran is not the candidate's committed %s" % WITNESS_REL
    rel = facts.rel(row["script"])
    if rel is None or row["script_sha256"] != facts.blob(rel):
        return RED, "the hook script %s that ran is not the candidate's committed blob" % row["script"]
    if row["tool_sha256"] != facts.tools:
        return RED, "tool_sha256 is not the candidate's committed %s" % ", ".join(TOOL_SCRIPTS)
    try:
        installed = _sha256(_read(row["hooks_path"]))
    except (OSError, ValueError):
        return NO_DATA, "the installed hook file %s the host read is gone: re-run the host (%s)" % (
            row["hooks_path"], OWNER_COMMAND)
    if installed != row["hooks_sha256"]:
        return RED, "the installed hook file %s no longer hashes to hooks_sha256" % row["hooks_path"]
    expected = facts.hooks_expected(row["host"])
    if row["hooks_expected_sha256"] != expected or row["hooks_sha256"] != expected:
        return RED, "the installed hook file is not the candidate's %s hook file (hooks_sha256 %s, expected %s)" % (
            row["host"], row["hooks_sha256"][:12], expected[:12])
    if not _bare_name(row["shim_path"]):
        return RED, "shim_path %s is not a file name beside the evidence" % _show(row["shim_path"])
    try:
        shim = _sha256(_read(os.path.join(row["_evidence_dir"], row["shim_path"])))
    except OSError:
        return RED, "the shim copy %s is missing beside the evidence" % row["shim_path"]
    if shim != row["shim_sha256"] or shim != _shim_sha256(row["real_python"]):
        return RED, "the shim is not the bytes write_shim produces for %s" % row["real_python"]
    return 0, ""


def _crosscheck(rows, root):
    """(code, why) for the Claude Code run: HP1.e's parse_hook_events re-reads the stored stream against the
    CANDIDATE's shipped allowlist (<root>/bundle, the hooks the rows ran, never this checkout's) and its crosscheck
    must agree with the shim rows. While HP1.e is not in the tree this is NO-DATA, never a pass."""
    try:
        import host_live_claude as claude   # HP1.e: the host's own account of its hook fires
    except ImportError:
        return NO_DATA, HP1E_MISSING
    refs = [(r.get("stream_path"), r.get("stream_sha256")) for r in rows]
    name, digest = refs[0]
    if any(ref != refs[0] for ref in refs) or not _is_sha256(digest):
        return RED, "the run's rows do not name one stored stream (stream_path, stream_sha256)"
    try:
        stream = _read(os.path.join(rows[0]["_evidence_dir"], name)) if _bare_name(name) else None
    except OSError:
        stream = None
    if stream is None or _sha256(stream) != digest:
        return RED, "the stored stream %s is missing or does not hash to stream_sha256" % _show(name)
    shim_rows = [dict((k, v) for k, v in r.items() if k not in LOADER_KEYS) for r in rows]
    try:
        result = claude.crosscheck(shim_rows, claude.parse_hook_events(stream, os.path.join(root, "bundle")))
    except (ValueError, LookupError, TypeError, AttributeError) as exc:
        return NO_DATA, "the HP1.e crosscheck could not run: %s: %s" % (type(exc).__name__, exc)
    if not isinstance(result, tuple) or len(result) != 2 or result[0] is not True:
        return RED, "the host's stream and the shim rows disagree: %s" % (
            result[1] if isinstance(result, tuple) and len(result) == 2 else _show(result))
    return 0, ""


def _manifest_reason(path):
    """'' when <evidence>.sha256, which only the collector writes (host_live_proof.write_manifest), holds the sha256 of
    the evidence file as it stands; else why not. Rows name every other file by sha256, so this binds the folder: a
    row or file edited by hand after collection reads NO-DATA. What stays trusted: a folder forged whole with its
    manifest recomputed is owner machine trust, not something this file can tell apart (spec 13, question 4)."""
    try:
        recorded = _read(path + proof.MANIFEST_SUFFIX).decode("ascii", "replace").strip()
    except OSError:
        return "%s has no manifest %s: only the collector writes one, so these rows were not collected" % (
            path, os.path.basename(path) + proof.MANIFEST_SUFFIX)
    if recorded != _sha256(_read(path)):
        return "%s differs from the manifest the collector wrote: a row or file was edited after collection" % path
    return ""


def _newest_runs(rows):
    """({host: rows of its newest run_id}, rows ignored as older runs)."""
    newest = {}
    for row in rows:
        key, stamp = (row["host"], row["run_id"]), _stamp(row["recorded_at"])
        newest[key] = max(newest.get(key, stamp), stamp)
    chosen = {}
    for (host, run_id), stamp in newest.items():
        if host not in chosen or (stamp, run_id) > chosen[host]:
            chosen[host] = (stamp, run_id)
    runs = {}
    for row in rows:
        if chosen[row["host"]][1] == row["run_id"]:
            runs.setdefault(row["host"], []).append(row)
    return runs, len(rows) - sum(len(v) for v in runs.values())


def _judge(path, root, now, required, scope=None):
    """(code, line) judging the hosts of required; scope (default required) is the whole deciding set, so a host in
    scope but not in required is skipped silently and only a known host outside scope is reported aside."""
    scope = required if scope is None else scope
    rows, problem = load_rows(path)
    if problem:
        return (NO_DATA if problem.startswith("NO-DATA") else RED), problem
    judged, extra = [], {}
    aside = dict((host, 0) for host in REQUIRED_HOSTS if host not in scope)   # known hosts outside the deciding scope
    for row in rows:
        host = row.get("host")
        if isinstance(host, str) and host in required:
            judged.append(row)
        elif isinstance(host, str) and host in scope:
            continue   # another deciding host, judged on its own (verify's per host lines)
        elif isinstance(host, str) and host in aside:
            aside[host] += 1   # reported, never judged, never a pass (the 1.1.0 scope: DECIDING_HOSTS_1_1_0)
        elif isinstance(host, str) and EXTRA_HOST.match(host):
            extra[host] = extra.get(host, 0) + 1
        else:
            return RED, "RED: line %d names an unknown host %s: a row comes from %s or a named extra host" % (
                row["_line"], _show(host), ", ".join(REQUIRED_HOSTS))
    notes = [ASIDE_NOTE % (h, aside[h]) for h in sorted(aside)] if scope == required else []
    notes += ["ignored %d row(s) of extra host %s" % (extra[h], h) for h in sorted(extra)]

    def say(code, line):
        return code, line + ("; " + "; ".join(notes) if notes else "")

    for row in judged:
        why = _shape(row)
        if why:
            return say(RED, "RED: %s line %d: %s" % (row["host"], row["_line"], why))
    by_run = {}
    for row in judged:
        by_run.setdefault(row["run_id"], set()).add(row["plugin_tree_sha256"])
    for run_id in sorted(by_run):
        if len(by_run[run_id]) > 1:
            return say(RED, "RED: run %s carries %d candidates: one run_id is recorded at one candidate" % (
                run_id, len(by_run[run_id])))
    runs, older = _newest_runs(judged)   # one candidate across hosts: each row is bound to the candidate (_binding)
    if older:
        notes.append("ignored %d row(s) of older runs" % older)
    probes, starts, tools, others = {}, {}, {}, 0
    for host in required:
        for row in runs.get(host, []):
            why = _integrity(row)
            if why:
                return say(RED, "RED: %s line %d: %s" % (host, row["_line"], why))
            if row["probe"] == "other":
                if host in RULED_NO_DATA and row["event"] in ("SessionStart", "PreToolUse"):   # what it owes instead
                    why = _origin(row)
                    if why:
                        return say(RED, "RED: %s line %d (%s): %s" % (host, row["_line"], row["event"], why))
                    (starts if row["event"] == "SessionStart" else tools).setdefault(host, []).append(row)
                else:
                    others += 1
                continue
            why = _origin(row)
            if why:
                return say(RED, "RED: %s line %d (%s): %s" % (host, row["_line"], row["probe"], why))
            probes.setdefault(host, []).append(row)
    if others:
        notes.append("ignored %d row(s) labelled other" % others)
    absent = missing_hosts(judged, required)
    if absent:
        return say(NO_DATA, "NO-DATA: no row from %s: the owner run records it: %s" % (", ".join(absent), OWNER_COMMAND))
    for host in required:
        if host not in runs:
            continue
        have = {row["probe"] for row in probes.get(host, [])}
        ruled = RULED_NO_DATA.get(host, ())
        lacking = [p for p in REQUIRED_PROBES + EXTRA_PROBES.get(host, ()) if p not in have and p not in ruled]
        if ruled and host not in starts:
            lacking.append("SessionStart")
        if lacking:
            return say(NO_DATA, "NO-DATA: %s run %s has no %s row: the owner run records it: %s" % (
                host, runs[host][0]["run_id"], ", ".join(lacking), OWNER_COMMAND))
        unrecorded = [p for p in ruled if p not in have]
        if unrecorded:
            notes.append("%s %s: NO-DATA by %s, not judged, never a pass" % (
                host, " and ".join(unrecorded), RULED_NO_DATA_REF[host]))
        for row in probes.get(host, []) if ruled else []:   # the PreToolUse leg, cross-checked against its own turn
            if row["event"] == "PreToolUse" and not _same_session(row, "model_turn"):
                return say(NO_DATA, "NO-DATA: %s line %d (%s): the PreToolUse row's session_id is not its turn's "
                                    "thread_id, so the host's stream does not show this hook fire: %s" % (
                                        host, row["_line"], row["probe"], OWNER_COMMAND))
    for host in required:
        if host in probes:
            why = signed_in_reason(host, probes[host])
            if why:
                return say(RED, "RED: %s not signed in: %s" % (host, why))
    try:
        facts = _reading(_Candidate, root)
        for host in required:
            for row in probes.get(host, []) + starts.get(host, []):
                code, why = _binding(row, facts, now)
                if why:
                    return say(code, "RED: %s line %d (%s): %s" % (host, row["_line"], row["probe"], why))
        if facts.dirty:
            return say(RED, "RED: dirty: the checkout %s differs from the candidate under plugin, bundle, products or "
                            "the tool scripts: %s" % (root, ", ".join(facts.dirty[:5])))
        for host in required:
            for row in probes.get(host, []) + starts.get(host, []):
                code, why = _bytes_ran(row, facts)
                if why:
                    word = "RED" if code == RED else "NO-DATA"
                    return say(code, "%s: %s line %d (%s): %s" % (word, host, row["_line"], row["probe"], why))
        for host in required:   # the ruling's GREEN rests on the transcript showing the door answered
            if "door" in RULED_NO_DATA.get(host, ()) and "door" not in {r["probe"] for r in probes.get(host, [])}:
                whys = [_door_answered(row, tools.get(host, []), facts) for row in starts[host]]
                if all(whys):
                    return say(NO_DATA, "NO-DATA: %s run %s: no transcript shows the door answered (%s): %s" % (
                        host, runs[host][0]["run_id"], whys[0], OWNER_COMMAND))
                notes.append("%s door transcript answered" % host)
    except VerifyNoData as exc:
        return say(NO_DATA, "NO-DATA: the candidate at %s cannot be judged: %s" % (root, exc))
    code, why = _crosscheck(runs["claude"], root) if "claude" in runs else (GREEN, "")
    if why:
        return say(code, "%s: claude: %s" % ("RED" if code == RED else "NO-DATA", why))
    why = _manifest_reason(path)
    if why:
        return say(NO_DATA, "NO-DATA: %s" % why)
    done = ", ".join("%s run %s" % (host, runs[host][0]["run_id"]) for host in required if host in runs)
    return say(GREEN, "GREEN: %s: %d rows fresh at candidate %s (last plugin commit %s), host originated, host launched, "
                      "signed in, guarded writes as EXPECTED_GUARDED_WRITE says" % (
                          done, sum(len(v) for v in probes.values()) + sum(len(v) for v in starts.values()),
                          facts.tree_sha256[:12], _iso(facts.last_commit)))


def verify(path: str, root: str, now: int, required=DECIDING_HOSTS_1_1_0) -> Tuple[int, str]:
    """(code, line) for the evidence file at path judged against the checkout root at time now (seconds since the
    epoch): 0 GREEN, 1 RED, 3 NO-DATA. Prints the one line. The function the cut gate calls. `required` names the hosts
    that decide (default DECIDING_HOSTS_1_1_0; REQUIRED_HOSTS is the 1.1.1 bar): a known host outside it is reported
    by ASIDE_NOTE and never judged, never a pass. Each deciding host is judged on its own first, so one host's missing
    leg never hides another's state: when any is not GREEN the line is the worst verdict (RED over NO-DATA) followed by
    every host's own line; when all are GREEN the hosts are judged together (one candidate, the sealed manifest) and
    that line is returned. Hostile arguments are refused (VerifyRefused), never judged."""
    _need_text(path, "path")
    _need_text(root, "root")
    _need_now(now)
    required, root = _need_required(required), os.path.abspath(root)
    whole = len(required) > 1 and not load_rows(path)[1]   # an unreadable file is one answer for every host
    each = [(host,) + _judge(path, root, now, (host,), required) for host in required] if whole else []
    if any(code != GREEN for _host, code, _line in each):
        code = RED if any(c == RED for _h, c, _l in each) else NO_DATA
        hosts = [r.get("host") for r in load_rows(path)[0]]
        line = "%s: per host: %s" % ("RED" if code == RED else "NO-DATA", " | ".join(
            ["%s: %s" % (host, text) for host, _c, text in each] +
            [ASIDE_NOTE % (h, hosts.count(h)) for h in REQUIRED_HOSTS if h not in required]))
    else:
        code, line = _judge(path, root, now, required)
    print(line)
    return code, line


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise VerifyRefused("main: argv must be a list of strings")
    parser = argparse.ArgumentParser(prog="host_live_verify.py", description="HP1 evidence verifier: 0 GREEN, 1 RED, "
                                     "3 NO-DATA, one line naming the host and the condition.")
    parser.add_argument("evidence", help="the evidence file, docs/plan/evidence/HP1-host-live.jsonl")
    parser.add_argument("--root", default=os.path.dirname(HERE), help="the candidate checkout (default: this one)")
    args = parser.parse_args(argv)
    try:
        return verify(args.evidence, args.root, int(time.time()))[0]
    except VerifyRefused as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
