# Antigravity E2E test log

## Schema

One record per fired event. Field names are exact.

| Field | Type | Values |
|---|---|---|
| run_id | str | 32 hex |
| environment | str | sandbox or real |
| started_at | str | ISO8601 UTC |
| host_name | str | verbatim or no_data |
| host_version | str | verbatim or no_data |
| os_platform | str | linux, darwin, windows |
| plugin_path_abs | str | absolute path |
| manifest_sha256 | str | 64 hex |
| hooks_sha256 | str | 64 hex |
| mcp_config_sha256 | str | 64 hex |
| event_name | str | one of the 5 wired names |
| event_sequence | int | 1 to 5, monotonic per run_id |
| tool_name | str | verbatim or no_data |
| tool_input_sha256 | str | 64 hex or no_data |
| raw_in_path | str | path |
| raw_in_sha256 | str | 64 hex |
| raw_out_path | str | path |
| raw_out_sha256 | str | 64 hex |
| decision_verbatim | str | exact host read value, or empty string |
| decision_kind | str | allow, deny, ask, force_ask, terminate, continue, no_data |
| adapter_invoked | bool | true or false |
| adapter_exit_code | int | 0 to 255, or -1 when not invoked |
| adapter_wall_ms | int | wall milliseconds |
| stderr_path | str | path |
| outcome | str | pass, fail, no_data |

Load result fields: ok bool, host_name str, host_version str, load_argv list of
str, load_exit_code int, manifest str, mcp_config str, skills_count int,
rules_count int, reason str. manifest and mcp_config take ok, fail or no_data.

## L1b.0 host contract schema

Captured from the real hook adapter. Host facts are read from disk when they
exist; a fact that could not be read is recorded as the literal string no_data
and is reported by the gate, so absence never reads as the safe case. Nothing
here is a host claim: host_name, host_version, load_argv, resolved_cwd and
matcher_proof stay no_data until a host is available, and no load verb is
asserted in this sub unit.

```json
{
  "benign_pre_tool_stdin": "no_data",
  "benign_pre_tool_stdout": "no_data",
  "event_shape": {
    "post_invocation": {
      "invocationNum": "int"
    },
    "pre_invocation": {
      "invocationNum": "int"
    },
    "stop": {
      "fullyIdle": "bool",
      "terminationReason": "str"
    }
  },
  "handler_outputs": {
    "post_invocation": {
      "injectSteps": [],
      "terminationBehavior": ""
    },
    "post_tool": {},
    "pre_invocation": {
      "injectSteps": []
    },
    "pre_tool": {
      "decision": "allow"
    },
    "stop": {
      "decision": "allow"
    }
  },
  "hooks_byte_length": "no_data",
  "hooks_sha256": "no_data",
  "host_name": "no_data",
  "host_version": "no_data",
  "known_tools": [
    "grep_search",
    "list_dir",
    "multi_replace_file_content",
    "replace_file_content",
    "run_command",
    "view_file",
    "write_to_file"
  ],
  "load_argv": "no_data",
  "main_modes": [
    "PostInvocation",
    "PostToolUse",
    "PreInvocation",
    "PreToolUse",
    "Stop",
    "post_invocation",
    "post_tool",
    "pre_invocation",
    "pre_tool",
    "stop"
  ],
  "manifest_sha256": "no_data",
  "matcher_proof": "no_data",
  "max_stdin_chars": 1000000,
  "mcp_config_sha256": "no_data",
  "plugin_path_abs": "no_data",
  "resolved_cwd": "no_data",
  "rules_count": "no_data",
  "skills_count": "no_data",
  "tool_map_keys": [
    "grep_search",
    "list_dir",
    "multi_replace_file_content",
    "replace_file_content",
    "run_command",
    "view_file",
    "write_to_file"
  ]
}
```

## L1b.7 run and write log

`tests/e2e/antigravity/run_e2e.py` exposes `run_sandbox` and `write_log`.
`run_sandbox` resolves or accepts a host binary, copies the source plugin
into a scratch sandbox, runs the load gate, fires the five wired events
in order 1 to 5, and returns a run result carrying the run id, the
environment, the started at time, the load result and enriched event
entries. Every event entry carries `hook_cwd_abs` equal to the directory
of the sandbox `hooks.json`, or the sandbox root when no hooks.json is
present. `write_log` writes one JSON object per line to the log path; a
non dict run result, a run result without an events list, a non string
or empty log path and any non dict event entry are refused with
`ValueError`, never a raw interpreter exception.

## L1b.6 doc linter anchor rules

`tests/e2e/antigravity/doc_lint.py` lints the install document against this
log. A claim is a fenced command block, or a sentence carrying either of the
two lint trigger words (the past tense forms of "check" and "confirm"). Each
claim must carry an anchor `<!-- evidence: <raw_out_sha256> -->` on the same
line or the line before, and that anchor must resolve to a log entry.

`lint_doc` returns ok false, with a reason, when a claim has no anchor, when
an anchor is not found in the log, when a claim states that arguments are
inspected, that stop is enforced, or that MCP is served, when an anchor entry
pins a host_version, hooks_sha256 or manifest_sha256 that differs from log
entry 1 of the same run_id, and when an anchor points at a corrupt post_tool
or pre_invocation entry carrying outcome pass.

Hostile input is refused with the module's own ValueError and never as a raw
interpreter exception: a non-string, empty or null byte path, a missing,
directory or unreadable file, bytes that are not utf-8, a log that is not
JSON or holds no entries, and a log nested too deeply for the standard
library scanner. A log entry whose run_id is unhashable (a JSON list or
object) is corrupt input: it can never join a pinned run, so any anchor
pointing at it fails the version pin check rather than raising TypeError
through the linter.

`doc_lint.main` refuses a missing document or log with exit code 2, and
reports a lint failure with exit code 1.
