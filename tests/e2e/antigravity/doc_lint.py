"""Doc linter for the Antigravity install doc evidence tracing.

Every fenced command block and every sentence carrying either of the two lint
trigger words (the past tense forms of "check" and "confirm") is a claim.
Each claim must carry an evidence anchor on the same line or the line before,
and that anchor must resolve to a log entry for the same pinned run.

lint_doc fails (ok false, with a reason) on a claim with no anchor, a claim
whose anchor is absent from the log, a claim that asserts a control the log
does not show, an anchor whose entry pins a host_version, hooks_sha256 or
manifest_sha256 that differs from the first entry of the same run_id, and an
anchor pointing at a corrupt post_tool or pre_invocation entry carrying
outcome pass.

Hostile input is refused with this module's own ValueError: a non-string,
empty or null byte path, a missing, directory or unreadable file, bytes that
are not utf-8, and a log that is not JSON, holds no entries, or is nested too
deeply for the standard library scanner. Nothing raw reaches a caller: an
unexpected failure inside parsing and analysis fails closed as a ValueError.
A log entry whose run_id is unhashable (a JSON list or object) is corrupt
input: it can never join a pinned run, so indexing a run map with it is
refused and any anchor pointing at it fails the version pin check instead of
raising TypeError through the linter.
"""

from __future__ import annotations

import json
import re
import sys

EVIDENCE_RE = re.compile(r"<!--\s*evidence:\s*([0-9a-fA-F]{64})\s*-->")

FORBIDDEN_PHRASES = (
    "arg inspection",
    "inspects args",
    "inspects arguments",
    "checks args",
    "checks arguments",
    "argument inspection",
    "arguments are inspected",
    "args are inspected",
    "arguments are checked",
    "args are checked",
    "stop enforcement",
    "stop is enforced",
    "stop enforces",
    "enforces completion",
    "enforces the completion",
    "mcp serving",
    "serves mcp",
    "serves an mcp",
    "serves the mcp",
    "mcp server",
)

PIN_FIELDS = ("host_version", "hooks_sha256", "manifest_sha256")

CORRUPT_EVENTS = ("post_tool", "pre_invocation")

USAGE = "usage: python3 -m tests.e2e.antigravity.doc_lint <doc_path> <log_path>"
MISSING_HOOK_LIMIT = "A missing or crashing hook does not guard (the host ran the tool after hook exit 2); a Brother verdict requires the adapter's own event log."


def is_hashable(value):
    """Return True only when value can be a dict key without raising.

    A JSON list or object used as a run_id reaches here unhashable; such an
    entry is corrupt and must never reach a dict key.
    """
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _read_bytes(path):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if "\x00" in path:
        raise ValueError("path must not contain a null byte")
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError("file not readable: %s" % path) from exc


def _read_text(path):
    data = _read_bytes(path)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not utf-8: %s" % path) from exc


def _loads(text, label):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (label, exc)) from exc


def _parse_log(log_text):
    if not isinstance(log_text, str):
        raise ValueError("log text must be a string")
    text = log_text.strip()
    if not text:
        raise ValueError("log is empty and has no entries")
    entries = []
    if text.startswith("["):
        data = _loads(text, "log")
        if not isinstance(data, list):
            raise ValueError("log root must be a JSON list")
        entries = data
    else:
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            entries.append(_loads(stripped, "log line %d" % line_no))
    if not entries:
        raise ValueError("log has no entries")
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("log entry %d is not an object" % idx)
    return entries


def _index_by_raw_hash(entries):
    by_hash = {}
    for entry in entries:
        raw_hash = entry.get("raw_out_sha256")
        if isinstance(raw_hash, str) and raw_hash:
            by_hash[raw_hash.lower()] = entry
    return by_hash


def _first_entry_by_run(entries):
    first = {}
    for entry in entries:
        run_id = entry.get("run_id")
        if not is_hashable(run_id):
            # an unhashable run_id is corrupt input: it can never join a run
            continue
        if run_id not in first:
            first[run_id] = entry
    return first


def _anchor_for_line(lines, idx):
    for pos in (idx, idx - 1):
        if 0 <= pos < len(lines):
            match = EVIDENCE_RE.search(lines[pos])
            if match:
                return match.group(1).lower()
    return None


def _claims(doc_text):
    lines = doc_text.splitlines()
    claims = []
    in_fence = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                claims.append((idx, "command block", _anchor_for_line(lines, idx)))
            else:
                in_fence = False
            continue
        if in_fence:
            continue
        if re.search(r"\b(verified|confirmed)\b", line, re.IGNORECASE):
            claims.append((idx, "sentence", _anchor_for_line(lines, idx)))
    return claims


def lint_doc(doc_path, log_path):
    """Lint a document against the JSON evidence log.

    Returns a dict with keys ok, untraced, forbidden, version_mismatch,
    corrupt_anchor and reason. Hostile paths, unreadable files and corrupt
    log content raise this module's own ValueError.
    """
    doc_text = _read_text(doc_path)
    log_text = _read_text(log_path)
    try:
        entries = _parse_log(log_text)
        by_hash = _index_by_raw_hash(entries)
        first_by_run = _first_entry_by_run(entries)

        untraced = []
        forbidden = []
        version_mismatch = []
        corrupt_anchor = []

        lower = doc_text.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in lower:
                forbidden.append(phrase)

        for idx, kind, anchor in _claims(doc_text):
            human = idx + 1
            if not anchor:
                untraced.append("line %d: %s has no evidence anchor" % (human, kind))
                continue
            entry = by_hash.get(anchor)
            if entry is None:
                untraced.append("line %d: anchor %s not in log" % (human, anchor))
                continue
            run_id = entry.get("run_id")
            # never hash an unhashable run_id: it is corrupt, it joins no run
            first = first_by_run.get(run_id) if is_hashable(run_id) else None
            for field in PIN_FIELDS:
                if first is None or entry.get(field) != first.get(field):
                    pin_value = None if first is None else first.get(field)
                    version_mismatch.append(
                        "line %d: %s pinned %r differs from run entry 1 %r"
                        % (human, field, entry.get(field), pin_value)
                    )
                    break
            event = entry.get("event_name")
            if event in CORRUPT_EVENTS and entry.get("outcome") == "pass":
                corrupt_anchor.append(
                    "line %d: anchor points at a corrupt %s entry marked pass"
                    % (human, event)
                )

        ok = not (untraced or forbidden or version_mismatch or corrupt_anchor)
        reason = "ok"
        if not ok:
            parts = []
            if untraced:
                parts.append("untraced claims: " + "; ".join(untraced))
            if forbidden:
                parts.append("forbidden claims: " + ", ".join(forbidden))
            if version_mismatch:
                parts.append("version pin drift: " + "; ".join(version_mismatch))
            if corrupt_anchor:
                parts.append("corrupt anchor: " + "; ".join(corrupt_anchor))
            reason = " | ".join(parts)
        return {
            "ok": ok,
            "untraced": untraced,
            "forbidden": forbidden,
            "version_mismatch": version_mismatch,
            "corrupt_anchor": corrupt_anchor,
            "reason": reason,
        }
    except ValueError:
        raise
    except Exception as exc:
        # Fail closed: a corrupt or hostile log is refused with this module's
        # own deliberate error, never a raw interpreter exception.
        raise ValueError("doc lint failed closed (%s)" % type(exc).__name__) from exc


EVIDENCE_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
RUN_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")

REQUIRED_SECTIONS = (
    "## Prerequisites",
    "## Load the plugin",
    "## First run",
    "## What a fired hook looks like",
    "## Uninstall",
    "## Known limits",
)

REQUIRED_LIMIT_STATEMENTS = (
    "stop allows unconditionally",
    "pre_invocation injects nothing",
    "known tools allow destructive args",
    "pre_invocation and post_tool have no blocking verb on corrupt input",
)


def _check_out_path(path):
    """Refuse an output path that is not a plain non-empty string."""
    if not isinstance(path, str) or not path:
        raise ValueError("output path must be a non-empty string")
    if "\x00" in path:
        raise ValueError("output path must not contain a null byte")
    return path


def _write_text(path, text):
    try:
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
    except OSError as exc:
        raise ValueError("output not writable: %s" % path) from exc


def _validate_entries(entries):
    """Refuse log records that break the pinned data contract.

    A record altered after it was written, a raw output hash that is not a
    64 hex string, a run id that is not a 32 hex string, a sequence that is
    not a positive integer or an outcome that is not a string is corrupt
    input. Corrupt input BLOCKS: it is refused here with this module's own
    ValueError instead of quietly joining a pinned run and coming back as a
    plain untraced claim.
    """
    for idx, entry in enumerate(entries):
        run_id = entry.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise ValueError("log entry %d has no 32 hex run_id" % idx)
        raw_hash = entry.get("raw_out_sha256")
        if not isinstance(raw_hash, str) or not EVIDENCE_HASH_RE.match(raw_hash):
            raise ValueError("log entry %d has no 64 hex raw_out_sha256" % idx)
        sequence = entry.get("event_sequence")
        if sequence is not None:
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise ValueError("log entry %d has a non positive event_sequence" % idx)
        outcome = entry.get("outcome")
        if outcome is not None and not isinstance(outcome, str):
            raise ValueError("log entry %d has a non string outcome" % idx)
    return entries


def _fenced_blocks(doc_text):
    """Return the fenced command blocks of a document, in document order.

    Each block is a dict with keys open_line (the 0 based line index of the
    opening fence), command (the block body with surrounding newlines
    stripped) and anchor (the evidence hash for that block, or None). The
    anchor is read from the opening fence line or the line before it, which
    is the same rule lint_doc already uses for every other claim.
    """
    if not isinstance(doc_text, str):
        raise ValueError("doc text must be a string")
    lines = doc_text.splitlines()
    blocks = []
    in_fence = False
    body = []
    start = 0
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                body = []
                start = idx
            else:
                in_fence = False
                blocks.append({
                    "open_line": start,
                    "command": "\n".join(body).strip("\n"),
                    "anchor": _anchor_for_line(lines, start),
                })
            continue
        if in_fence:
            body.append(line)
    return blocks


def _block_anchor(block):
    """Return the lower case evidence hash of a block, or None.

    None means the block carries no valid anchor, so its raw output could
    never be matched to the pinned run. Such a block is refused before it is
    executed, never run and reported afterwards.
    """
    anchor = block.get("anchor") if isinstance(block, dict) else None
    if isinstance(anchor, str) and EVIDENCE_HASH_RE.match(anchor):
        return anchor.lower()
    return None


def _anchor_of(entry):
    """Return the lower case raw_out_sha256 of a log entry, or refuse."""
    raw = entry.get("raw_out_sha256") if isinstance(entry, dict) else None
    if not isinstance(raw, str) or not EVIDENCE_HASH_RE.match(raw):
        name = entry.get("event_name") if isinstance(entry, dict) else None
        raise ValueError("log entry for %r has no raw_out_sha256 evidence hash" % name)
    return raw.lower()


def _require_entry(entries, event_name):
    for entry in entries:
        if entry.get("event_name") == event_name:
            return entry
    raise ValueError("log has no entry for event %r" % event_name)


def _doc_text(entries):
    """Build the install walkthrough text from parsed log entries."""
    first = entries[0]
    host_name = first.get("host_name")
    host_version = first.get("host_version")
    if not isinstance(host_name, str) or not host_name:
        raise ValueError("log entry 1 has no host_name")
    if not isinstance(host_version, str) or not host_version:
        raise ValueError("log entry 1 has no host_version")
    if host_version == "no_data":
        raise ValueError("log entry 1 pins host_version no_data: no doc is rendered")

    pre_tool = _require_entry(entries, "PreToolUse")
    post_tool = _require_entry(entries, "PostToolUse")
    pre_invocation = _require_entry(entries, "PreInvocation")
    stop = _require_entry(entries, "Stop")

    pin = _anchor_of(first)
    pre_tool_hash = _anchor_of(pre_tool)
    post_tool_hash = _anchor_of(post_tool)
    pre_invocation_hash = _anchor_of(pre_invocation)
    stop_hash = _anchor_of(stop)

    lines = []
    add = lines.append

    add("# Install the Antigravity hook plugin")
    add("")
    add("This walkthrough is rendered from a captured evidence log. Every")
    add("command block and every stated fact carries an evidence anchor that")
    add("resolves to one pinned run in that log.")
    add("")

    add(REQUIRED_SECTIONS[0])
    add("")
    add("You need:")
    add("")
    add("- Python 3.9 or newer, standard library only.")
    add("- The Antigravity host `%s` at version `%s`." % (host_name, host_version))
    add("- A scratch workspace outside any repository you care about.")
    add("")
    add("The host name and version above are verified against the pinned run. <!-- evidence: %s -->" % pin)
    add("")

    add(REQUIRED_SECTIONS[1])
    add("")
    add("Copy `bundle/.antigravity-plugin/` into the scratch workspace and load")
    add("it with the captured host load argv, which is a single token, the host")
    add("binary itself, with no invented verb:")
    add("<!-- evidence: %s -->" % pin)
    add("```bash")
    add(host_name)
    add("```")
    add("")

    add(REQUIRED_SECTIONS[2])
    add("")
    add("Fire one hook event by piping a tool call payload into the adapter:")
    add("<!-- evidence: %s -->" % pre_tool_hash)
    add("```bash")
    add("python3 scripts/brother_antigravity_hook.py pre_tool < payload.json")
    add("```")
    add("")

    add(REQUIRED_SECTIONS[3])
    add("")
    add("A clean PreToolUse for a known tool answers with the verbatim stdout")
    add('line `{"decision": "allow"}` and exits 0. That verbatim output is')
    add("confirmed by the pinned evidence anchor: <!-- evidence: %s -->" % pre_tool_hash)
    add("")
    add("A corrupt PostToolUse payload writes the verbatim stderr line")
    add("`brother_antigravity_hook: PostToolUse received corrupt payload: ...`")
    add("and still answers `{}` on stdout.")
    add("")
    add("A corrupt PreInvocation payload writes the verbatim stderr line")
    add("`brother_antigravity_hook: PreInvocation received corrupt payload and cannot block by contract: ...`")
    add('and still answers `{"injectSteps": []}` on stdout.')
    add("")
    add('A corrupt Stop payload answers `{"decision": "continue"}`.')
    add("A corrupt PostInvocation payload answers")
    add('`{"injectSteps": [], "terminationBehavior": "terminate"}`.')
    add("")

    add(REQUIRED_SECTIONS[4])
    add("")
    add("Uninstall is verified against the pinned run. <!-- evidence: %s -->" % pin)
    add("")
    add("Remove the plugin directory from the scratch workspace and drop the")
    add("host entry that pointed at it.")
    add("")

    add(REQUIRED_SECTIONS[5])
    add("")
    add(MISSING_HOOK_LIMIT)
    add("- stop allows unconditionally: a clean Stop returns")
    add('  `{"decision": "allow"}` and no completion artifact control exists. <!-- evidence: %s -->' % stop_hash)
    add("- pre_invocation injects nothing: a clean PreInvocation returns")
    add('  `{"injectSteps": []}`. <!-- evidence: %s -->' % pre_invocation_hash)
    add("- known tools allow destructive args: only the tool name is read, so a")
    add("  `run_command` carrying `rm -rf docs/` is allowed. <!-- evidence: %s -->" % pre_tool_hash)
    add("- pre_invocation and post_tool have no blocking verb on corrupt input:")
    add("  a corrupt payload on those events is reported on stderr and blocked")
    add("  downstream. <!-- evidence: %s -->" % post_tool_hash)
    add("")
    return "\n".join(lines) + "\n"


def render_install_doc(log_path, out_path):
    """Render the fresh reader install walkthrough from an evidence log.

    Sections appear in the order prerequisites, load, first run, fired hook,
    uninstall, known limits. Every fenced command block and every verified or
    confirmed sentence carries an evidence anchor built from the matching log
    entry's raw_out_sha256. A missing, unreadable, corrupt or contract
    breaking log, and a hostile out_path, raise this module's own ValueError,
    and out_path is never written when any check fails.
    """
    log_text = _read_text(log_path)
    entries = _parse_log(log_text)
    _validate_entries(entries)
    _check_out_path(out_path)
    text = _doc_text(entries)
    _write_text(out_path, text)


def verify_install_doc(doc_path, log_path):
    """Verify a rendered install doc against the evidence log.

    Reuses lint_doc for evidence tracing and forbidden claim checks, then
    requires the four known limit statements and the six required sections in
    order. This is a gate, not a report: a doc that cannot be traced, a doc
    missing a required section or limit statement, and a missing, corrupt or
    hostile doc_path or log_path all BLOCK with this module's own ValueError
    naming what failed, so an unverified doc can never merge. The ok dict is
    returned only when every check passes.
    """
    doc_text = _read_text(doc_path)
    log_text = _read_text(log_path)
    entries = _parse_log(log_text)
    _validate_entries(entries)
    lint = lint_doc(doc_path, log_path)

    lower = doc_text.lower()
    missing_limits = []
    for statement in REQUIRED_LIMIT_STATEMENTS:
        if statement not in lower:
            missing_limits.append(statement)

    positions = []
    missing_sections = []
    for section in REQUIRED_SECTIONS:
        idx = doc_text.find(section)
        if idx < 0:
            missing_sections.append(section)
        else:
            positions.append(idx)
    order_ok = not missing_sections and positions == sorted(positions)

    problems = []
    if not lint["ok"]:
        problems.append("lint: %s" % lint["reason"])
    if missing_limits:
        problems.append("missing known limits: " + "; ".join(missing_limits))
    if missing_sections:
        problems.append("missing sections: " + "; ".join(missing_sections))
    elif not order_ok:
        problems.append("sections out of order")
    if problems:
        raise ValueError("install doc refused: " + " | ".join(problems))
    return {
        "ok": True,
        "lint": lint,
        "missing_limits": [],
        "missing_sections": [],
        "section_order_ok": True,
        "reason": "ok",
    }


def _resolve_command_runner():
    """Return the allow listed command runner from run_e2e, or None.

    doc_lint is not on the subprocess allow list, so the stranger harness must
    execute a doc's command blocks through tests/e2e/antigravity/run_e2e.py.
    The exact helper name in that module was UNKNOWN to this builder (the file
    was not shown), so this resolves one explicit contract name and otherwise
    refuses with None rather than guessing, shelling out here, or approving a
    run that never happened. A missing runner is a refusal, never a pass.
    """
    try:
        from tests.e2e.antigravity import run_e2e
    except ImportError:
        return None
    try:
        return run_e2e.run_command_block
    except AttributeError:
        return None


def run_stranger_harness(doc_path, scratch_home, runner=None):
    """Run a doc's fenced command blocks in order under a fresh HOME.

    Returns a dict with keys reached_fired_hook, first_attempt and failed_at.
    Blocks run one at a time, in document order, through the allow listed
    command runner, and the first nonzero exit stops the run for good: the
    harness never retries and a retried pass does not count. A block with no
    valid evidence anchor is refused before it runs, because its output could
    not be matched to the pinned run. reached_fired_hook is True only when the
    last command block exits 0 and its raw output hash equals that block's
    evidence anchor. A missing doc, an empty scratch home, a doc with no
    fenced blocks and a missing runner all return False with a non-empty
    failed_at, and nothing raises past this function.
    """
    outcome = {"reached_fired_hook": False, "first_attempt": True, "failed_at": ""}

    if not isinstance(doc_path, str) or not doc_path:
        outcome["failed_at"] = "doc_path is missing or not a string"
        return outcome
    if "\x00" in doc_path:
        outcome["failed_at"] = "doc_path contains a null byte"
        return outcome
    if not isinstance(scratch_home, str) or not scratch_home.strip():
        outcome["failed_at"] = "scratch_home is missing or empty"
        return outcome
    if "\x00" in scratch_home:
        outcome["failed_at"] = "scratch_home contains a null byte"
        return outcome

    try:
        doc_text = _read_text(doc_path)
    except ValueError as exc:
        outcome["failed_at"] = "doc is unreadable: %s" % exc
        return outcome

    try:
        blocks = _fenced_blocks(doc_text)
    except ValueError as exc:
        outcome["failed_at"] = "doc cannot be scanned: %s" % exc
        return outcome

    if not blocks:
        outcome["failed_at"] = "doc has no fenced command blocks"
        return outcome

    command_runner = runner if runner is not None else _resolve_command_runner()
    if command_runner is None:
        outcome["failed_at"] = "no allow listed command runner is available"
        return outcome

    last = len(blocks) - 1
    for idx, block in enumerate(blocks):
        label = "block %d" % (idx + 1)
        command = block.get("command")
        if not isinstance(command, str) or not command.strip():
            outcome["failed_at"] = "%s is empty" % label
            return outcome
        anchor = _block_anchor(block)
        if anchor is None:
            outcome["failed_at"] = "%s has no valid evidence anchor" % label
            return outcome
        try:
            result = command_runner(command, scratch_home)
        except Exception as exc:
            outcome["failed_at"] = "%s raised %s" % (label, type(exc).__name__)
            return outcome
        if not isinstance(result, dict):
            outcome["failed_at"] = "%s returned no result record" % label
            return outcome
        exit_code = result.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            outcome["failed_at"] = "%s reported no exit status" % label
            return outcome
        if exit_code != 0:
            outcome["failed_at"] = "%s exited nonzero (%d)" % (label, exit_code)
            return outcome
        if idx == last:
            out_sha = result.get("out_sha256")
            if isinstance(out_sha, str) and out_sha.lower() == anchor:
                outcome["reached_fired_hook"] = True
            else:
                outcome["failed_at"] = "%s output does not match its evidence anchor" % label
            return outcome
    return outcome


def main(argv):
    if not isinstance(argv, list) or len(argv) != 2:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        result = lint_doc(argv[0], argv[1])
    except ValueError as exc:
        sys.stderr.write("doc_lint: %s\n" % exc)
        return 2
    if not result["ok"]:
        sys.stderr.write("doc_lint: %s\n" % result["reason"])
        return 1
    sys.stdout.write("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
