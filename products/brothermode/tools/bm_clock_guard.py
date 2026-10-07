"""M1.1 clock guard contracts and parsing."""
import json
import pathlib
import re
import sys

_TIME_RE = re.compile(r"\b(?:[01][0-9]|2[0-3]):[0-5][0-9]\b")
_ESTIMATE_RE = re.compile(r"\b(?:estimate|estimated|approx|eta|placeholder|example|fake)\b", re.IGNORECASE)
_BOUNDARY = ".!?" + chr(10) + ";"

def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be str")
    return value

def _require_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be int")
    return value

def find_times(text: str) -> list[tuple[str, int, int, int, int]]:
    text = _require_text(text, "text")
    found = []
    for match in _TIME_RE.finditer(text):
        token = match.group(0)
        hour = int(token[0:2])
        minute = int(token[3:5])
        found.append((token, match.start(), match.end(), hour, minute))
    return found

def is_estimate_labeled(text: str, start: int, end: int) -> bool:
    text = _require_text(text, "text")
    start = _require_int(start, "start")
    end = _require_int(end, "end")
    if start < 0 or end < start or end > len(text):
        raise ValueError("start and end must be a valid slice")
    left = start
    while left > 0 and text[left - 1] not in _BOUNDARY:
        left -= 1
    right = end
    while right < len(text) and text[right] not in _BOUNDARY:
        right += 1
    statement = text[left:right]
    rel_start = start - left
    rel_end = end - left
    for keyword in _ESTIMATE_RE.finditer(statement):
        k_start, k_end = keyword.span()
        if k_end <= rel_start:
            gap = rel_start - k_end
        elif k_start >= rel_end:
            gap = k_start - rel_end
        else:
            gap = 0
        if gap <= 24:
            return True
    return False

def to_minutes(h: int, m: int) -> int:
    h = _require_int(h, "h")
    m = _require_int(m, "m")
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError("hour or minute out of range")
    return h * 60 + m

def minute_diff(a_min: int, b_min: int) -> int:
    a_min = _require_int(a_min, "a_min")
    b_min = _require_int(b_min, "b_min")
    if a_min < 0 or a_min > 1439 or b_min < 0 or b_min > 1439:
        raise ValueError("minute out of range")
    raw = abs(a_min - b_min)
    direct = raw % 1440
    return min(direct, 1440 - direct)

CLOCK_COMMAND_ALLOWLIST: tuple[str, ...] = ("date", "clock", "bm_clock")
CLOCK_RESULT_FIELDS: tuple[str, ...] = ("stdout", "content", "text", "tool_response")
GUARD_REASONS: tuple[str, ...] = (
    "OK",
    "NO-TIME",
    "NO-DATA",
    "UNREADABLE",
    "CORRUPT",
    "MISMATCH",
    "ESTIMATE-ONLY",
    "OVERSIZED",
)
_TOLERANCE_MINUTES = 2

def _require_minutes(value: object, name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be list of int")
    for item in value:
        if type(item) is not int:
            raise ValueError(f"{name} items must be int")
    return value

def parse_clock_evidence(transcript_text: str) -> list[int]:
    transcript_text = _require_text(transcript_text, "transcript_text")
    minutes: list[int] = []
    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Footprint (2026-09-30): this runs on every Bash, Edit and Write call
        # and at Stop, over the whole transcript (113 MB on the largest one
        # measured). A record can only decode to type "tool_result" when the
        # raw line carries that literal, or a \u escape that could spell it
        # (letters and underscore have no short JSON escape), so every other
        # line is skipped before json.loads. Same result set as before, by
        # construction; test_bm_clock_guard_prefilter.py pins both halves.
        if "tool_result" not in line and "\\u" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") != "tool_result":
            continue
        command = record.get("command")
        if not isinstance(command, str):
            continue
        tokens = command.split()
        if not tokens or tokens[0] not in CLOCK_COMMAND_ALLOWLIST:
            continue
        result_text = None
        for field in CLOCK_RESULT_FIELDS:
            candidate = record.get(field)
            if isinstance(candidate, str):
                result_text = candidate
                break
        if result_text is None:
            continue
        for _token, _start, _end, hour, minute in find_times(result_text):
            minutes.append(hour * 60 + minute)
    return minutes

def decide(outgoing_text: str, clock_minutes: list[int]) -> tuple[str, str]:
    outgoing_text = _require_text(outgoing_text, "outgoing_text")
    clock_minutes = _require_minutes(clock_minutes, "clock_minutes")
    candidate_times = find_times(outgoing_text)
    if not candidate_times:
        return ("ALLOW", "NO-TIME")
    required: list[tuple[str, int, int]] = []
    for token, start, end, hour, minute in candidate_times:
        if not is_estimate_labeled(outgoing_text, start, end):
            required.append((token, hour, minute))
    if not required:
        return ("ALLOW", "ESTIMATE-ONLY")
    if not clock_minutes:
        return ("BLOCK", "NO-DATA")
    offenders: list[str] = []
    for token, hour, minute in required:
        target = to_minutes(hour, minute)
        if not any(minute_diff(target, evidence) <= _TOLERANCE_MINUTES for evidence in clock_minutes):
            offenders.append(token)
    if offenders:
        return ("BLOCK", "MISMATCH")
    return ("ALLOW", "OK")

def format_block(reason: str, times: list[str]) -> str:
    reason = _require_text(reason, "reason")
    if not isinstance(times, list):
        raise ValueError("times must be list of str")
    for item in times:
        if not isinstance(item, str):
            raise ValueError("times must be list of str")
    return json.dumps({"decision": "BLOCK", "reason": reason, "times": times})


_MAX_OUTGOING_CHARS = 20000
_FILE_EDIT_TOOLS: tuple[str, ...] = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _concat_file_outgoing(tool_name: str, tool_input: dict) -> tuple[str, str]:
    if tool_name == "NotebookEdit":
        file_path = tool_input.get("notebook_path")
    else:
        file_path = tool_input.get("file_path")
    if not isinstance(file_path, str):
        return ("", "CORRUPT")
    parts: list[str] = [file_path]
    if tool_name == "Write":
        content = tool_input.get("content")
        if not isinstance(content, str):
            return ("", "CORRUPT")
        parts.append(content)
    elif tool_name == "Edit":
        new_string = tool_input.get("new_string")
        if not isinstance(new_string, str):
            return ("", "CORRUPT")
        parts.append(new_string)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return ("", "CORRUPT")
        for entry in edits:
            if not isinstance(entry, dict):
                return ("", "CORRUPT")
            new_string = entry.get("new_string")
            if not isinstance(new_string, str):
                return ("", "CORRUPT")
            parts.append(new_string)
    elif tool_name == "NotebookEdit":
        new_source = tool_input.get("new_source")
        if not isinstance(new_source, str):
            return ("", "CORRUPT")
        parts.append(new_source)
    return ("\n".join(parts), "")


def extract_outgoing(payload: dict) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        return ("", "CORRUPT")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ("", "CORRUPT")
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return ("", "CORRUPT")
        text = command
    elif tool_name in _FILE_EDIT_TOOLS:
        text, file_reason = _concat_file_outgoing(tool_name, tool_input)
        if file_reason:
            return ("", file_reason)
    else:
        return ("", "CORRUPT")
    if len(text) > _MAX_OUTGOING_CHARS:
        return ("", "OVERSIZED")
    return (text, "")


def _read_transcript_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError("NO-DATA") from exc


def _record_id(record: dict) -> object:
    candidate = record.get("id")
    if candidate is None:
        candidate = record.get("tool_use_id")
    return candidate


def extract_evidence(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str):
        raise ValueError("NO-DATA")
    raw = _read_transcript_bytes(transcript_path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("NO-DATA") from exc
    lines = text.splitlines()
    parsed: list[object] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            parsed.append(None)
            continue
        try:
            parsed.append(json.loads(stripped))
        except ValueError:
            parsed.append(None)
    last_user = -1
    stop_at = len(lines)
    current_id = payload.get("tool_use_id")
    for index, record in enumerate(parsed):
        if not isinstance(record, dict):
            continue
        if record.get("type") == "user":
            last_user = index
        if isinstance(current_id, str) and index > last_user:
            if _record_id(record) == current_id:
                stop_at = index
                break
    kept: list[str] = []
    for index in range(last_user + 1, stop_at):
        record = parsed[index]
        if isinstance(record, dict) and record.get("type") == "tool_result":
            kept.append(lines[index])
    return "\n".join(kept)


def _times_to_report(outgoing_text: str) -> list[str]:
    report: list[str] = []
    for token, start, end, _hour, _minute in find_times(outgoing_text):
        if not is_estimate_labeled(outgoing_text, start, end):
            report.append(token)
    return report


def _last_assistant_text(transcript_text: str) -> str:
    """The text of the last assistant record in a transcript, or "" when
    there is none. Record shape as scripts/intake_measure.py reads it:
    {"type": "assistant", "message": {"content": [{"type": "text", ...}]}}."""
    transcript_text = _require_text(transcript_text, "transcript_text")
    found = ""
    for raw_line in transcript_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or '"assistant"' not in stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"
                             and isinstance(b.get("text"), str))
        else:
            continue
        if text.strip():
            found = text
    return found


def extract_stop_text(payload: dict) -> tuple[str, str]:
    """The text a Stop payload asks this guard to check, and a reason when
    it cannot be had: the host's own last_assistant_message when it gives
    one (Codex), else the last assistant text in the transcript (Claude
    Code), else "" with reason "" (nothing to check). A field of the wrong
    type is "CORRUPT"; a transcript the host named but this guard cannot
    read is "NO-DATA"."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")
    last = payload.get("last_assistant_message")
    if isinstance(last, str):
        return (last, "")
    if last is not None:
        return ("", "CORRUPT")
    transcript_path = payload.get("transcript_path")
    if transcript_path is None:
        return ("", "")
    if not isinstance(transcript_path, str):
        return ("", "CORRUPT")
    try:
        text = _read_transcript_bytes(transcript_path).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ("", "NO-DATA")
    return (_last_assistant_text(text), "")


def _stop_verdict(payload: dict) -> int:
    """The Stop answer, in the host's own spelling. Before 2026-10-04 a Stop
    payload (which carries no tool_name) was read as CORRUPT outgoing text
    and the guard wrote {"decision": "BLOCK", ...} at every turn end on
    every host: a host honouring a Stop block kept the agent from stopping,
    one rejecting the spelling showed a hook error. Now: nothing to check,
    or no transcript to check against (Codex gives none), allows with the
    reason on stderr; a bare time the transcript does not support refuses
    with {"decision": "block", "reason": ...}, the documented Stop contract
    sbe_session_reconcile.py uses, once per turn (stop_hook_active breaks
    the loop); malformed input is reported, never a block."""
    try:
        text, text_reason = extract_stop_text(payload)
    except Exception:
        text, text_reason = "", "CORRUPT"
    if text_reason:
        _warn("bm_clock_guard: %s at Stop: the last assistant message could not be read, "
              "so no time in it was checked" % text_reason)
        return 0
    try:
        required_times = _times_to_report(text)
    except Exception:
        _warn("bm_clock_guard: CORRUPT at Stop: the last assistant message could not be scanned")
        return 0
    if not required_times:
        return 0
    if payload.get("transcript_path") is None:
        _warn("bm_clock_guard: NO-DATA at Stop: this host gives no transcript, so the time(s) %s "
              "in the last assistant message could not be checked against a clock" % ", ".join(required_times))
        return 0
    # ponytail: the transcript is read whole here (extract_evidence always
    # has) and once more above for the last assistant text, so a Stop costs
    # two linear passes over the file; a tail read with a record boundary
    # scan if transcripts of tens of MB ever make that bite.
    try:
        evidence_minutes = parse_clock_evidence(extract_evidence(payload))
    except ValueError:
        evidence_minutes = []
    except Exception:
        _warn("bm_clock_guard: CORRUPT at Stop: the transcript could not be parsed for clock evidence")
        return 0
    try:
        decision, reason = decide(text, evidence_minutes)
    except Exception:
        _warn("bm_clock_guard: CORRUPT at Stop: the last assistant message could not be judged")
        return 0
    if decision == "ALLOW":
        return 0
    message = ("BrotherMode clock guard: %s: the time(s) %s in the last message were not read from a clock "
               "in this turn; run date (or clock) first and quote it, or label the time as an estimate"
               % (reason, ", ".join(required_times)))
    if reason != "MISMATCH":
        # NO-DATA never blocks a stop: an unreadable transcript, or one with
        # no clock read in it, leaves the time unverified, not disproved.
        # Only a clock that was read and contradicts the text refuses.
        _warn("bm_clock_guard: " + message)
        return 0
    if payload.get("stop_hook_active") is True:
        _warn("bm_clock_guard: already continued once this turn, not blocking again: %s" % message)
        return 0
    sys.stdout.write(json.dumps({"decision": "block", "reason": message}) + "\n")
    return 0


def _load_bm_learning():
    """Load bm_learning.py by path, the shape bm_telemetry.py's
    _load_bm_learning uses: works from any cwd and never raises."""
    try:
        import importlib.util
        here = pathlib.Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "bm_learning_for_clock_guard", str(here / "bm_learning.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional module load; the caller writes a constant line instead
        return None


def _warn(text: str) -> None:
    """One stderr line through the print choke point (bm_learning.one_line),
    which tools/test_bm_print_choke_point.py requires of every stream write
    carrying outside text. Exit code and stdout are untouched, so a report
    line never becomes a verdict."""
    mod = _load_bm_learning()
    if mod is None:
        sys.stderr.write("bm_clock_guard: a report line was dropped because bm_learning.py could not be loaded\n")
        return
    sys.stderr.write(mod.one_line(text) + "\n")


def _emit_guard_verdict(payload: object, decision: str, reason: str, times: list[str]) -> int:
    hook_event = None
    if isinstance(payload, dict):
        hook_event = payload.get("hook_event_name")
    if hook_event == "PreToolUse":
        if decision == "BLOCK":
            body = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
            sys.stdout.write(json.dumps(body) + "\n")
        return 0
    body_text = json.dumps({"decision": decision, "reason": reason, "times": times})
    if decision == "BLOCK":
        sys.stderr.write(body_text + "\n")
        sys.stdout.write(body_text + "\n")
        return 2
    sys.stdout.write(body_text + "\n")
    return 0


def selftest() -> tuple[bool, str]:
    decision, reason = decide("meet at 09:41", [])
    if decision != "BLOCK" or reason != "NO-DATA":
        return (False, f"selftest: positive block failed: {decision}/{reason}")
    base = to_minutes(9, 41)
    decision, reason = decide("meet at 09:41", [base])
    if decision != "ALLOW" or reason != "OK":
        return (False, f"selftest: negative allow failed: {decision}/{reason}")
    return (True, "selftest: block and allow cases behave")


def selftest_mutation_probe() -> tuple[bool, str]:
    base = to_minutes(9, 41)
    shifted = (base + 30) % 1440
    decision, reason = decide("meet at 09:41", [shifted])
    if decision != "BLOCK":
        return (False, f"mutation probe: shifted evidence returned {decision}/{reason}")
    return (True, "mutation probe: shifted evidence blocks")


def verify_installed(root: str) -> tuple[bool, str]:
    if not isinstance(root, str):
        return (False, "root must be str")
    root_path = pathlib.Path(root)
    guard_path = root_path / "tools" / "bm_clock_guard.py"
    hooks_path = root_path / "hooks" / "hooks.json"
    test_path = root_path / "tools" / "test_bm_clock_guard.py"
    if not guard_path.is_file():
        return (False, f"guard missing: {guard_path}")
    try:
        guard_source = guard_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return (False, f"guard unreadable: {exc}")
    try:
        compile(guard_source, str(guard_path), "exec")
    except SyntaxError as exc:
        return (False, f"guard does not compile: {exc}")
    if not hooks_path.is_file():
        return (False, f"hooks missing: {hooks_path}")
    try:
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return (False, f"hooks unreadable: {exc}")
    if not isinstance(hooks_data, dict):
        return (False, "hooks not an object")
    hooks_section = hooks_data.get("hooks")
    if not isinstance(hooks_section, dict):
        return (False, "hooks section missing")
    for event in ("PreToolUse", "Stop"):
        entries = hooks_section.get(event)
        if not isinstance(entries, list):
            return (False, f"{event} missing")
        matched = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if isinstance(command, str) and "bm_clock_guard" in command:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return (False, f"guard not wired in {event}")
    if not test_path.is_file():
        return (False, f"test file missing: {test_path}")
    return (True, "installed copy verified")


def verify_key_components(root: str) -> tuple[bool, str]:
    if not isinstance(root, str):
        return (False, "root must be str")
    path = pathlib.Path(root) / "docs" / "plan" / "KEY-COMPONENTS.json"
    if not path.is_file():
        return (False, f"key components missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return (False, f"key components unreadable: {exc}")
    if not isinstance(data, dict):
        return (False, "key components not an object")
    components = data.get("components")
    if not isinstance(components, list):
        return (False, "components missing")
    for entry in components:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name is None:
            name = entry.get("id")
        if name != "clock-guard":
            continue
        # The repository registry's convention (scripts/key_components.py, 18 entries): "tool" is the component and
        # "guard" the test that proves it, both from the repository root (2026-09-28: this read the guard as the tool).
        if entry.get("tool") != "products/brothermode/tools/bm_clock_guard.py":
            return (False, "clock-guard tool mismatch")
        if entry.get("guard") != "products/brothermode/tools/test_bm_clock_guard.py":
            return (False, "clock-guard guard mismatch")
        if entry.get("hooks") != ["PreToolUse", "Stop"]:
            return (False, "clock-guard hooks mismatch")
        return (True, "clock-guard entry verified")
    return (False, "clock-guard entry missing")


def _load_bm_repo_scope():
    """Load bm_repo_scope.py by path, the same load-by-path shape every hook
    in this directory uses for a sibling module (bm_session_cap.py's loader
    is the template). E76 per-repository hook scoping and E50 scoped
    installs: consulted once the envelope is decoded and before any verdict,
    so a repository with hooks off, or one nobody opted in, gets no output
    from this guard at all. A loader failure returns None and the guard
    stays active, because a scoping check must never be the reason a real
    guard stops working."""
    try:
        import importlib.util
        here = pathlib.Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_for_clock_guard", str(here / "bm_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def main(argv: list[str]) -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    payload = None
    try:
        candidate = json.loads(raw)
    except Exception:
        candidate = None
    if isinstance(candidate, dict):
        payload = candidate
    _rs = _load_bm_repo_scope()
    if _rs is not None and _rs.hooks_off(payload=payload):
        return 0
    # FAIL DIRECTION. This guard checks clock text; it is not an access
    # control. It denies (the PreToolUse deny, or the exit 2 verdict of the
    # command line mode) ONLY when the payload is a tool call: hook_event_name
    # PreToolUse, or a tool_name present. Input it cannot read, or a payload
    # that is not a tool call (a Stop or any other event with no tool_name),
    # is reported on stderr at exit 0 and never answered "BLOCK" or exit 2:
    # hooks.json wires this guard on Stop with no matcher, a host honouring
    # exit 2 at Stop keeps the agent from stopping, and with unreadable input
    # stop_hook_active cannot be read to break that loop. Trapping a stop is
    # the worse failure, so unreadable input fails toward the report.
    if payload is None:
        _warn("bm_clock_guard: CORRUPT: stdin was not a JSON object, so nothing was checked")
        return 0
    event = payload.get("hook_event_name")
    if event == "Stop":
        return _stop_verdict(payload)
    if event != "PreToolUse" and not isinstance(payload.get("tool_name"), str):
        _warn("bm_clock_guard: CORRUPT: a %s payload with no tool_name is not a tool call, so nothing was checked"
              % (event if isinstance(event, str) else "nameless"))
        return 0
    try:
        text, outgoing_reason = extract_outgoing(payload)
    except Exception:
        return _emit_guard_verdict(payload, "BLOCK", "CORRUPT", [])
    if outgoing_reason:
        return _emit_guard_verdict(payload, "BLOCK", outgoing_reason, [])
    # Nothing to verify means nothing to read: an outgoing text with no bare
    # clock time makes no claim this guard checks, so the transcript is not
    # opened for it. Before 2026-10-04 the transcript was read first and its
    # absence blocked every call; Codex sends "transcript_path": null on
    # every hook payload (HP1 live run, 2026-10-04), so the guard denied
    # every Bash, Edit and Write call there, including a read of Brother's
    # own installed skill. A bare time with no transcript still blocks NO-DATA.
    try:
        required_times = _times_to_report(text)
        if not required_times:
            decision, verdict_reason = decide(text, [])
            return _emit_guard_verdict(payload, decision, verdict_reason, [])
    except Exception:
        return _emit_guard_verdict(payload, "BLOCK", "CORRUPT", [])
    try:
        evidence_text = extract_evidence(payload)
    except ValueError:
        return _emit_guard_verdict(payload, "BLOCK", "NO-DATA", [])
    except Exception:
        return _emit_guard_verdict(payload, "BLOCK", "CORRUPT", [])
    try:
        evidence_minutes = parse_clock_evidence(evidence_text)
        decision, verdict_reason = decide(text, evidence_minutes)
        report_times = _times_to_report(text)
    except Exception:
        return _emit_guard_verdict(payload, "BLOCK", "CORRUPT", [])
    return _emit_guard_verdict(payload, decision, verdict_reason, report_times)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
