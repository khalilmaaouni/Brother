#!/usr/bin/env python3
"""R25.1: the limit watcher. Classifies the LAST record of a session
transcript as NORMAL or one of four measured limit classes, and (in --arm
mode) writes the restart flag and schedules the launchd restart.

Built against REAL records on this machine (2026-08-30), not an assumed
contract: `grep -rl '"isApiErrorMessage":true' ~/.claude/projects` turned up
every case. A limit rejection always carries isApiErrorMessage true, error
"rate_limit", apiErrorStatus 429, and a message.content text block. Beyond
that the four classes disagree in a way the proposal's contract undersold:

  - "You've hit your session limit ... resets HH:MM (Asia/Tokyo)": the real
    five_hour class. On every one of 44 measured records, quotaLimits was
    null: no epoch anywhere, only a bare clock time in the text. resets_at
    is therefore null for this class on this machine (NO-DATA-safe, never
    guessed by parsing a clock time with no date attached).
  - "You've hit your weekly limit ... resets Mon DD at H(am/pm)": the
    seven_day class. quotaLimits.rateLimitType == "seven_day" and
    .resetsAt (epoch seconds) were both present in most measured records.
  - "You've hit your monthly spend limit ... raise it at claude.ai/...":
    the monthly-spend class MISLABELS itself in the structured field,
    measured verbatim: quotaLimits.rateLimitType == "five_hour" even
    though the text says monthly spend. The message text overrides the
    structured field for classification, and resets_at is set null even
    when quotaLimits carries one, because only a human raising the cap at
    the named settings URL fixes this, never a timed restart.
  - "You've reached your Fable 5 limit. Run /usage-credits ... or switch
    models": the model-credit class. quotaLimits null AND no reset time
    anywhere, structured or in text. Maps to class "fallback-model": the
    remedy is falling back to another model per the standing cap, not
    waiting.

Any other isApiErrorMessage record (auth failures, 529 overloaded,
connection drops) is NORMAL: not a limit, nothing to arm.

R25: on a seven_day (weekly) limit, arm() also emits the portable pack
(portable_pack.build_pack), per the founder's own words on R25: the person
must be able to continue in another account or on another machine. A pack
failure never aborts the arm; it is recorded in the returned dict instead.

Exit 0 on a successful classification (any class, including NORMAL).
Exit 2 (NO-DATA) when the transcript cannot be read at all. No em or en
dashes.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restart_schedule
import portable_pack

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FLAG_PATH = os.path.expanduser("~/.claude/brother-restart/armed.flag")

# Measured, not a generic URL parser: every monthly-spend record on this
# machine names claude.ai/settings/usage, with or without a scheme prefix.
URL_RE = re.compile(r"\S*\bclaude\.ai\S*")

# (substring to match in the lowered message text, resulting class). Order
# matters: "monthly spend" is checked first because one measured record's
# text carries both "monthly spend limit" and, later in the same string,
# "session limit resets ..." as a trailing clause.
LIMIT_TEXT_RULES = (
    ("monthly spend", "monthly-spend"),
    ("weekly limit", "seven_day"),
    ("session limit", "five_hour"),
)


def _message_text(record):
    message = record.get("message") or {}
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text") or ""
    return ""


def classify(record):
    """One transcript record -> {class, resets_at, message_url,
    raw_text_excerpt, remedy}. NORMAL for anything that is not a measured
    rate_limit rejection."""
    if not isinstance(record, dict):
        return {"class": "NORMAL", "resets_at": None, "message_url": None,
                "raw_text_excerpt": None, "remedy": None}

    text = _message_text(record)
    if not (record.get("isApiErrorMessage") and record.get("error") == "rate_limit"
            and record.get("apiErrorStatus") == 429):
        return {"class": "NORMAL", "resets_at": None, "message_url": None,
                "raw_text_excerpt": text[:200] if text else None,
                "remedy": None}

    lowered = text.lower()
    quota = record.get("quotaLimits") or {}

    for needle, cls in LIMIT_TEXT_RULES:
        if needle not in lowered:
            continue
        if cls == "monthly-spend":
            url_match = URL_RE.search(text)
            return {"class": cls, "resets_at": None,
                    "message_url": url_match.group(0) if url_match else None,
                    "raw_text_excerpt": text[:200], "remedy": None}
        return {"class": cls, "resets_at": quota.get("resetsAt"),
                "message_url": None, "raw_text_excerpt": text[:200],
                "remedy": None}

    # rate_limit/429 but none of the known texts matched: no reset time
    # anywhere, structured or textual (the model-credit / Fable-tier case).
    return {"class": "fallback-model", "resets_at": None, "message_url": None,
            "raw_text_excerpt": text[:200],
            "remedy": "fall back to the standing model cap per founder rule"}


def read_last_record(path):
    """The last parseable JSON line in a transcript. None when the file is
    missing, empty, or holds no parseable JSON line (never a guess)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:  # sbe: allow-silent a missing or unreadable transcript is the documented None/NO-DATA case, never an error to raise
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except ValueError:  # sbe: allow-silent transcripts legally hold partial trailing lines; the contract is last PARSEABLE line
            continue
    return None


def newest_transcript(project_dir):
    """The most recently modified *.jsonl in project_dir, or None."""
    try:
        candidates = [os.path.join(project_dir, name)
                      for name in os.listdir(project_dir)
                      if name.endswith(".jsonl")]
    except OSError:  # sbe: allow-silent an absent project dir means no transcript exists, the documented None case
        return None
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def watch(transcript_path=None, project_dir=None):
    """Resolve a transcript (explicit path, or newest in project_dir) and
    classify its last record. NO-DATA, never a guess, when nothing
    resolves or the file cannot be read."""
    path = transcript_path or (newest_transcript(project_dir) if project_dir else None)
    if not path:
        return {"class": "NO-DATA", "resets_at": None, "message_url": None,
                "raw_text_excerpt": None, "remedy": None,
                "error": "NO-DATA: no transcript path resolved"}
    record = read_last_record(path)
    if record is None:
        return {"class": "NO-DATA", "resets_at": None, "message_url": None,
                "raw_text_excerpt": None, "remedy": None,
                "error": "NO-DATA: transcript %s unreadable or empty" % path}
    return classify(record)


def resume_command(run_dir, repo_root=REPO_ROOT):
    """The exact resume command for the interrupted run directory, worded
    as an instruction because the restart flag is fed to `claude -p` as its
    prompt, not executed as a shell line (matches the existing
    ~/.claude/brother-restart/restart.sh, which this module does not
    change)."""
    brother_run = os.path.join(repo_root, "scripts", "brother_run.py")
    return ("Resume the interrupted run: python3 %s --resume %s"
            % (brother_run, os.path.abspath(run_dir)))


def arm(run_dir, result, flag_path=DEFAULT_FLAG_PATH, margin=120,
        schedule_fn=None, pack_fn=None):
    """Writes the restart flag with the resume command and schedules the
    launchd restart at the measured reset time. A NORMAL or NO-DATA result
    arms nothing: there is no limit to recover from.

    On a seven_day result, also calls pack_fn (defaults to
    portable_pack.build_pack, mirroring the schedule_fn seam) to emit the
    portable pack. A pack failure is recorded under the "pack" key and
    never aborts the arm: the flag and schedule matter more than the pack.
    Only OSError and ValueError (portable_pack's own documented NO-DATA
    exceptions) are caught; anything else propagates."""
    if result.get("class") in ("NORMAL", "NO-DATA"):
        return {"armed": False,
                "reason": "nothing to arm: class is %s" % result.get("class")}
    os.makedirs(os.path.dirname(flag_path), exist_ok=True)
    with open(flag_path, "w", encoding="utf-8") as f:
        f.write(resume_command(run_dir) + "\n")
    schedule_fn = schedule_fn or restart_schedule.schedule
    schedule_result = schedule_fn(result.get("resets_at"), margin=margin)
    out = {"armed": True, "flag_path": flag_path, "schedule": schedule_result}

    if result.get("class") == "seven_day":
        pack_fn = pack_fn or portable_pack.build_pack
        try:
            zip_path, had_no_data = pack_fn([REPO_ROOT])
        except (OSError, ValueError) as e:  # sbe: allow-silent portable_pack's own documented NO-DATA contract; the arm must not abort on it
            out["pack"] = "pack emission failed: %s" % e
        else:
            out["pack"] = zip_path
            if had_no_data:
                out["pack_degraded"] = ("a collected member was NO-DATA; "
                                        "the pack is partial, not clean")

    return out


def main(argv):
    transcript_path = None
    project_dir = None
    arm_run_dir = None
    margin = 120
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--project-dir" and i + 1 < len(argv):
            project_dir = argv[i + 1]; i += 1
        elif a == "--arm" and i + 1 < len(argv):
            arm_run_dir = argv[i + 1]; i += 1
        elif a == "--margin" and i + 1 < len(argv):
            margin = int(argv[i + 1]); i += 1
        else:
            positional.append(a)
        i += 1

    if positional:
        transcript_path = positional[0]

    result = watch(transcript_path=transcript_path, project_dir=project_dir)
    print(json.dumps(result, indent=2, sort_keys=True))

    if arm_run_dir:
        arm_result = arm(arm_run_dir, result, margin=margin)
        print("limit-watch: %s" % json.dumps(arm_result, sort_keys=True))

    return 2 if result["class"] == "NO-DATA" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
