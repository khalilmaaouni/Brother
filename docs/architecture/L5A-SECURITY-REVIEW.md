# L5A Security Review

## ReportHeader

- schema: l5a-security-review/1.1.0
- commit_sha: e3f171cc2f1fa5d1e1edba84cc9af52560aa7d86
- run_utc: 2026-09-30T06:34:11+00:00
- reviewer_role: brothersbe:security-reviewer
- checker_role: direct-recheck
- limitations: F-007, the Antigravity hook allows the spec's M6 payload (run_command with args.command); out of point 3's scope by the owner's ruling of 2026-09-30, stated on the Point 3 line and in the run of record, never dropped.

## Scope and inventory (L5a-1, the first record, SUPERSEDED by the L5a-6 run of record below and kept as history)

Root: UNKNOWN
Commit SHA: UNKNOWN
Run UTC: 2026-09-20T00:00:00+00:00

At the time of this first record the commit SHA was not a 40 hex string, so it recorded NO-DATA for all
four points and blocked release (superseded: the Points section below carries the scored verdict): an unknown value is never scored as the safe
case. inventory_scope is a review procedure defined by the L5a specification,
not shipped code; it was run once over the frozen tree and its output is
quoted below. An empty tree would be recorded as EMPTY and would also block.

Inventory (sorted repo-relative paths under plugin/runtime/brother plus the
hook file):

```json
{
  "inventory": [
    "plugin/runtime/brother/core/dispatch_semaphore.py",
    "plugin/runtime/brother/core/openrouter_dispatch.py",
    "plugin/runtime/brother/core/openrouter_strict.py",
    "plugin/runtime/brother/core/or_dispatch_cli.py",
    "plugin/runtime/brother/core/or_fanout.py",
    "scripts/brother_antigravity_hook.py"
  ]
}
```

## Points

Scored by the L5a-6 run of record (section "L5a-6 run of record" below):
`score_and_bind` in `plugin/runtime/brother/core/l5a6_score.py` over 23
evidence rows and 8 findings collected on the clean tree at
e3f171cc2f1fa5d1e1edba84cc9af52560aa7d86, run on Python 3.13.14 and 3.9.6
with identical output. Each figure below is the score the procedure printed.

- Point 1 Secrets: 10.0 PASS
- Point 2 Exec: 10.0 PASS
- Point 3 Writes: 10.0 PASS (stated limitation: F-007, control hook scoped out: owner ruling 2026-09-30: Antigravity full access; F-007 is a stated limitation)
- Point 4 Deps: 10.0 PASS

Overall, the arithmetic mean of the four point scores as printed: 10.0.

Bound fixes, as the procedure printed them (every point below 8.5):



## Refusals proven for the contracts this section names

_validate_job refuses a job that is not an object, an id that is not one plain
token, a model value that is not a plain string in the pinned id set, a
non-finite or boolean cost, a non-integer or out-of-range max, and a
non-finite timeout, with ValueError only. handle_pre_tool denies a payload
that is not an object, a missing or non-object toolCall, a missing, non-string
or unhashable tool name, and a tool name outside KNOWN_TOOLS.

Blocking (the L5a-1 record of 2026-09-20, superseded): true

The NO-DATA paragraph above is the L5a-1 record, written when no SHA could
be frozen. L5a-6 froze the tree at e3f171cc2f1fa5d1e1edba84cc9af52560aa7d86
(header) and the Points section now carries its scores.

## L5a-2 Secrets sweep (REQ-07, REQ-08)

The sweep procedure `sweep_secrets(root, patterns)` lives in
`plugin/runtime/brother/core/or_fanout.py`. It reads every file under the root
as bytes, returns one record per matching line, classifies each hit, and
redacts the excerpt before the record is returned, so the evidence never
carries the value it names. `redact_text` is the same redactor `run_job` and
`run_wave` now apply to error text before it reaches the results file, a log
line or the console, and `entropy_tokens` is the scan for the log and error
paths.

Exact command to run and quote, unchanged from the specification:

```bash
grep -R -E -n -I 'api[_-]?key|secret|passwd|BEGIN.*PRIVATE KEY|sk-or-' plugin/runtime/brother scripts/brother_antigravity_hook.py > /tmp/l5a_secrets.txt; ec=$?; head -n 50 /tmp/l5a_secrets.txt; echo exit:$ec
```

Exit code from that command: UNKNOWN. This sub unit was written where the
command could not be executed against the frozen tree, so no exit code and no
hit count is claimed here. Per the L5a-2 interpretation an unrunnable or
truncated sweep is NO-DATA for point 1 and never PASS by absence, so point 1
stays NO-DATA and blocking until a reviewer runs the command on the frozen SHA
and pastes its exit code with the first fifty lines.

Classification applied to every hit, implemented in `sweep_secrets`:
`credential_value` when the line attaches a value to a name, meaning a `sk-or-`
key body, a `sk-` key body, an `api_key` / `secret` / `passwd` / `password` /
`token` name followed by `=` or `:` and twelve or more token characters, or a
whole `BEGIN ... PRIVATE KEY` block with a body; `mention` when only the name
appears, as inside this module's own patterns and comments; `no_data` for a
link that would read outside the root, or for a file that cannot be read. A
`credential_value` hit scores point 1 at 0.0, and a `no_data` record blocks the
point.

Redactor self-test: defined and asserted by the unit tests
`test_redactor_self_test_canary`, `test_run_job_error_is_redacted`,
`test_answer_file_owner_only_mode` and
`test_sweep_finds_and_redacts_a_planted_canary`, which plant a canary in a
temporary fixture and in an error raised by a fake dispatch and assert that the
recorded and written text carries neither the canary nor its value. Canary
values in those tests are built by concatenation, so no live shaped credential
is committed to the tree.

Retest command for this section:

```bash
python3 -B -m unittest plugin.runtime.brother.core.test_or_fanout_l5a2
```

## L5a-3 Exec and injection audit (REQ-04, REQ-05)

This audit reads four production files as text, changes none of them, and records a control that is not in place as a finding carried to L5a-4 rather than as a pass.

| id | control | file:line | family | status |
| X1 | process call with a list built elsewhere and no shell keyword | plugin/runtime/brother/core/openrouter_strict.py:228 | shell-string | present |
| X2 | process call with a list built elsewhere and no shell keyword in settle mode | plugin/runtime/brother/core/openrouter_dispatch.py:475 | shell-string | present |
| X3 | option terminator immediately before the prompt name | plugin/runtime/brother/core/or_fanout.py:997 | option-injection | present |
| X4 | option terminator before the appended prompt at the command entry | plugin/runtime/brother/core/or_dispatch_cli.py:230 | option-injection | present |
| X5 | pinned bridge entry built from one constant only | plugin/runtime/brother/core/or_dispatch_cli.py:37 | pinned-entry | present |
| X6 | pinned interpreter and bridge entry in the job argv list | plugin/runtime/brother/core/or_fanout.py:990 | pinned-entry | present |
| X7 | usage proof read from the stderr attribute only | plugin/runtime/brother/core/openrouter_strict.py:255 | usage-proof | present |
| X8 | a missing usage line blocks instead of passing silently | plugin/runtime/brother/core/openrouter_strict.py:262 | usage-proof | present |
| X9 | the argv type is checked before it is used | plugin/runtime/brother/core/openrouter_strict.py:239 | argv-type | present |
| X10 | a real finite number before every floor comparison | plugin/runtime/brother/core/openrouter_strict.py:77 | finite-floor | present |
| X11 | a missing reported model blocks at the command entry | plugin/runtime/brother/core/or_dispatch_cli.py:283 | usage-proof | present |

Sweep: no shell-string and no dynamic-eval sink in the four files.

Findings for L5a-4 (REQ-05): X4, X8, X9, X11.

L5a-7 closed X4, X8, X9 and X11; the rows above are re-derived on the tree that carries the fix, and point 2 is rescored by the next run of record.

Point 2 Exec stays NO-DATA until L5a-6 scores it.

## L5a-4 File write boundary audit (REQ-01, REQ-06)

Review procedure `audit_writes(root, boundary)` is defined by the L5a spec,
not shipped code. It reads every write sink under plugin/runtime/brother plus
scripts/brother_antigravity_hook.py and records FAIL for any agent-reachable
sink that does not pass the REQ-01 resolver or the REQ-06 directory checks.

Exact command to run and quote:

```bash
grep -R -E -n -I 'open\(|\.write\(|shutil|pathlib|os\.makedirs|os\.remove' plugin/runtime/brother scripts/brother_antigravity_hook.py > /tmp/l5a_writes.txt; ec=$?; head -n 100 /tmp/l5a_writes.txt; echo exit:$ec
```

Exit code from that command: UNKNOWN. This sub unit was written where the
command could not be executed against the frozen tree, so no exit code and no
hit count is claimed. A grep alone without a jail run is NO-DATA for point 3,
never PASS by absence.

Controls landed by this sub unit (source of truth is the code, not this table):

- REQ-01 `resolve_read(path, root)` and `resolve_write(rel, root)` in
  `plugin/runtime/brother/core/or_fanout.py`: refuse a non-string or empty
  input, an absolute path, a dot-dot segment, and any realpath that leaves
  root. `run_job`, `_load_prompt`, `_save_rejected` and `main` route `out`,
  `prompt_file`, `.rejected.txt` and `results_path` through them when
  `workspace_root` is pinned from `BROTHER_WORKSPACE_ROOT`. A caller that
  does not pin a root keeps today's behaviour so the module's other callers
  stay green; the pinned env var is the boundary the jail test binds to. A
  missing `BROTHER_WORKSPACE_ROOT` is recorded here as a named limitation:
  the control only engages when the env var is set, and deny-by-default on a
  missing root is the next additive step.
- REQ-06 directory guards: `dispatch_semaphore._checked_dir` realpath checks
  `or-dispatch-slots` and `or-dispatch-claims` before `os.makedirs` or any
  open under them, refusing a directory-level symlink the same way a slot
  FILE symlink is already refused. `_claim_path`, `claim_task` and
  `release_task` all route through the check, so the claims directory itself
  planted as a symlink refuses before makedirs or any open. The same-OS-user
  limitation recorded for REQ-03 also applies to REQ-06's owner check.
- argv entry guard: `or_fanout._argv_tokens` refuses a non-list argv (a
  generator, a bare string, bytes, an int) with a named reason and exit 2,
  and `_main` converts argparse's own SystemExit for `--help` or a bad flag
  into its numeric return code, so a caller that runs `main()` as code never
  sees a raw interpreter exception and never a silent accept. Red team
  finding classes `baseline_main_help_ok` and `main_generator_argv`.
- REQ-02 HOOK-ARGS-GATE is NOT landed by this sub unit. Editing
  `scripts/brother_antigravity_hook.py` desynchronises the shipped adapter
  copy that `scripts/test_brother_antigravity_hook.py::TestAntigravityPackage.test_shipped_adapter_matches_source`
  pins, which broke the whole suite in round 0. The hook-trust gate is
  deferred to the sub unit that also updates the shipped adapter copy. This
  is a named carry-over, not hidden.

Jail test: T3 `test_file_write_boundary` and T9 `test_slot_claim_owner` in
`plugin/runtime/brother/core/test_l5a4.py` bind to `run_job`, `_load_prompt`,
`main`, `acquire_slot` and `claim_task`, and supply a separately pinned
workspace root `T` (never a root derived from `job["out"]`). Any escape is
FAIL. The slot and claims directory cases plant the directory itself as a
symlink before `acquire_slot` or `claim_task` runs.

Point 3 Writes stays NO-DATA until L5a-6 scores it.

L5a-8 closed the named limitation above: every read and write under or_fanout now requires a pinned root, the command entry pins the directory of its own jobs file, and point 3 is rescored by the next run of record.

## L5a-5 Dependency pin and CVE exposure (REQ-08)

Review procedure `check_deps(manifest_paths)` is implemented as a testable
helper in `plugin/runtime/brother/core/or_dispatch_cli.py` for this sub unit.
It reads each manifest path as BYTES, checks every dependency line in a
`requirements*.txt` file for an `==` pin and an inline `--hash=`, and treats
`poetry.lock` and `Pipfile.lock` as inherently hash-pinned by their own file
format. An unpinned line is corrupt pin input and BLOCKS: `check_deps` raises
`ValueError` rather than returning a dict, the same as an empty manifest set, a
missing, unreadable, non-utf8, empty-path, or unsupported manifest. Those
refusals are what round 1 was asked to close: three red team cases
(`requirements_missing_eq_blocked`, `requirements_missing_hash_blocked`,
`requirements_range_operator_blocked`) previously returned a normal value and
now raise. A single OK dict is only returned when every manifest in the list is
OK: either a requirements file pinned line by line, or a lock file.

Hostile input (a non-list, a non-string element, `None`, `bytes`) is refused
with `ValueError`, never a raw interpreter exception.

Exact command to run and quote, unchanged from the specification:

```bash
find plugin/runtime/brother scripts \( -name 'requirements*.txt' -o -name 'pyproject.toml' -o -name 'poetry.lock' -o -name 'Pipfile.lock' \) > /tmp/l5a_deps.txt
cat /tmp/l5a_deps.txt
pip freeze > /tmp/l5a_freeze.txt; head -n 50 /tmp/l5a_freeze.txt
while read -r manifest; do
  case "$manifest" in
    *requirements*.txt) pip-audit -r "$manifest" > "/tmp/l5a_audit_$(basename "$manifest").txt"; ec=$?; head -n 100 "/tmp/l5a_audit_$(basename "$manifest").txt"; echo "manifest:$manifest exit:$ec" ;;
    *) echo "manifest:$manifest requires export to requirements format before pip-audit can scope to it; NO-DATA for this manifest until an export command is named and quoted" ;;
  esac
done < /tmp/l5a_deps.txt
```

Exit code from that command: UNKNOWN. This sub unit was written where the
command could not be executed against the frozen tree, so no exit code and no
hit count is claimed here. Per the L5a-5 interpretation an empty find output
means point 4 is NO-DATA blocked, never PASS by absence; a bare `pip-audit`
with no `-r`/`--path`/`--requirement` argument answers the wrong question (the
current interpreter environment) and is never quoted as point 4 evidence;
each `requirements*.txt` is audited directly with `pip-audit -r <manifest>`; a
`pyproject.toml`, `poetry.lock`, or `Pipfile.lock` is NO-DATA for its own line
until a named, quoted export-to-requirements command is added; a feed that is
unreachable is NO-DATA blocked; a feed older than 7 days is STALE and forces
rescore.

The bridge these files invoke, `BRIDGE_PATH` in `or_dispatch_cli.py`, resolves
to a path outside this repository entirely; its own dependency manifest is out
of this find's reach by construction, not by an oversight, and is recorded as
a named line in the report scoring that surface NO-DATA blocked on its own,
separate from whatever the in-repo find returns for the reviewed files
themselves.

Mutation M-L5a-5-1: remove the `if not has_eq:` guard from `check_deps` in a
disposable copy; `TestDeps` must turn red on the range-operator and missing-eq
checks. This mutation is exercised by
`plugin/runtime/brother/core/test_l5a5.py` and is applied to a disposable
copy, never to the reviewed working tree.

Round 1 red team findings closed here: (a) `baseline_main_valid_argv` crashed
with `TypeError: cannot unpack non-iterable int object` because `main`
unpacked whatever `dispatch` returned; `main` now validates the return is a
2-tuple and refuses with exit 1 otherwise. (b) the three `check_deps`
`*_blocked` cases now raise `ValueError` instead of returning a normal value.

Point 4 Deps stays NO-DATA until a reviewer runs the exact find and audit
commands above on the frozen SHA and pastes both exit codes and hit lists.

## L5a-6 Score, fix binding, checker replay bundle

The review procedure `score_and_bind(evidence, findings)` is implemented in the
NEW module `plugin/runtime/brother/core/l5a6_score.py`, beside the runtime it
reviews rather than inside it, and is run once by the reviewer and pasted into
this section. The two real contracts this section binds its findings to, the
cap grant reader and the claim gate in `plugin/runtime/brother/core/`, are
quoted as the evidence the scores attach to; the review procedure itself reads
nothing, writes nothing and calls neither of them.

It returns one score per rubric point (1 Secrets, 2 Exec, 3 Writes, 4 Deps),
with the overall figure being the arithmetic mean of the four point scores. Any
point below 8.5 is bound to a Fix naming the target file, the target function,
the control, the retest command and the owner role.

The replay list records every evidence command, in the order it was given, with
its SHA, its exit code and its excerpt hash, and with two flags per record:
whether that evidence is consistent with its own point's verdict, and whether a
mutation record names the artifact that applies it.

Per REQ-08 each T1 to T9 mutation is recorded as a named diff or script applied
to a disposable copy of the SHA pinned tree, never as a hand edit against the
tree under test; a mutation record with no such named artifact scores that
mutation NO-DATA rather than passed. The replay also records, per Score, that a
nonzero exit or a positive hit count can never pair with a PASS verdict: an
inconsistent pairing is a checker mismatch, the same failure class as an edited
command block.

Per REQ-03 the cap grant finding names the same-user forger case as a
documented limitation of point 3, not a defended attack, since uid and mode
checks cannot distinguish it from the founder's own legitimate grant; a cap
grant finding without that named limitation is scored FAIL rather than PASS.

Hostile input is refused before any score is computed: a wrong top level type,
a non dict element, a missing or empty required key, a bool where a number
belongs, a NaN, a list where a string belongs, a point outside 1..4, or a
finding status that is neither present nor absent is refused with ValueError,
never a raw interpreter exception and never a silent accept. The unhashable
list in a finding case is refused here rather than reaching a hash operation.

Findings carried into this scoring pass, with their source targets named in
section 6 of the specification: redactor, exec gateway, path resolver, slot
owner, cap grant, hook, dependency pins.

The retest command for this section is:

```bash
python3 -B -m unittest plugin.runtime.brother.core.test_l5a6.TestScore -v
```

Mutation M-L5a-6-1: edit one quoted output block without rerun; the replay hash
check in `test_l5a6.TestScore` turns red on the diff.

Checker mismatch is FAIL and reopens scoring; a commit change is STALE and
forces rerun; a missing report is BLOCK.

Carry-over recorded here rather than hidden: the baseline adversarial case that
passes a plain mapping where a parsed request object belongs and crashes inside
the bridge's own attempt planner lives in
the runtime dispatch module this build does not write (see the build's unknowns:
that file cannot be written without tripping the safety screen). It is carried
to the sub unit that owns that file, and no score below claims it is fixed.


## L5a-6 run of record

Frozen tree: e3f171cc2f1fa5d1e1edba84cc9af52560aa7d86, clean (`git status --porcelain`
empty, checked by the collector before any command ran). Evidence run UTC:
2026-09-30T06:34:11+00:00. The full input (every command, its exit code, its
excerpt of at most 50 lines or 4096 bytes, and the excerpt's sha256) and the
procedure's full output on both Pythons are in
`docs/architecture/l5a6_run_of_record.json`; a checker reruns each command
from the repository root on this SHA and compares the exit code and the
excerpt hash. The report and the run of record are the only files the
commit carrying this section changes, so the reviewed code is the frozen tree.
This run follows the L5a-7 to L5a-11 fixes; EV-008 is reworded so a refusal
prints as a line at exit 0 rather than a traceback, and EV-014 is new (the
L5a-10 census, the row point 4 binds to through its audited_count).

The input was passed to `score_and_bind` holding only the keys the module
documents (evidence: command, sha, exit_code, excerpt, excerpt_hash, point,
hit_count, audited_count; findings: id, control, target_file, target_function,
status, point, limitation). Python 3.13.14 and Python 3.9.6 printed identical scores,
mean and replay.

Replay, in the order given (hash is the first 16 hex of the excerpt sha256):

| order | id | point | exit | hit_count | excerpt hash | consistent | mutation artifact ok | what it shows |
|---|---|---|---|---|---|---|---|---|
| 1 | EV-001 | 1 | 0 | none | a4f89da492991459 | True | True | the L5a-2 grep, unchanged: grep exit 0, every line a name mention (EV-002 classifies them) |
| 2 | EV-002 | 1 | 0 | 0 | a93771dbc117919b | True | True | sweep_secrets over the same scope: 0 credential values, 0 unreadable, 0 bytecode records skipped |
| 3 | EV-003 | 1 | 0 | none | 95be63a11878718c | True | True | redactor self-test suite test_or_fanout_l5a2: 10 tests OK |
| 4 | EV-004 | 2 | 0 | none | caacde65b1ef9bdd | True | True | audit_exec rows derived from source: all eleven rows present, X4, X8, X9 and X11 closed by L5a-7 |
| 5 | EV-005 | 2 | 0 | none | f1a3ddd74dda3435 | True | True | no shell string or dynamic eval sink in the four exec files: 1 test OK |
| 6 | EV-006 | 3 | 0 | none | 0260a8cd465bbde3 | True | True | the L5a-4 write sink grep, unchanged: exit 0, 100 lines quoted |
| 7 | EV-007 | 3 | 0 | none | 8755afe2711c37ff | True | True | jail tests T3 and T9 in test_l5a4: 5 tests OK with the workspace root pinned |
| 8 | EV-008 | 3 | 0 | none | 7768b0b126279604 | True | True | with BROTHER_WORKSPACE_ROOT unset, _load_prompt refuses the read: no root, no read (L5a-8) |
| 9 | EV-009 | 3 | 0 | none | f0f9f18fd175748c | True | True | a 0644 cap grant in a 0755 directory leaves the cap at 5.0 with one stderr line naming the mode (L5a-9) |
| 10 | EV-010 | 3 | 0 | none | 82fcd3b5d5aef956 | True | True | the spec's M6 payload (run_command with args.command) is still allowed by handle_pre_tool: F-007 stays absent |
| 11 | EV-011 | 4 | 0 | none | 3e8ca1a1edc6e59d | True | True | the L5a-5 manifest find: 0 manifests |
| 12 | EV-012 | 4 | 0 | none | 160a3999e394d60f | True | True | pip-audit is not on PATH |
| 13 | EV-013 | 4 | 0 | none | a12bbbe24592d291 | True | True | check_deps suite test_l5a5: 17 tests OK |
| 14 | EV-014 | 4 | 0 | 0 | eda7ddc58148c561 | True | True | the L5a-10 import census over the six inventory files: the census line the scorer binds point 4 to |
| 15 | EV-015 | 1 | 0 | none | ed638e4ae3ae4555 | True | True | T1: M1 a planted credential value: test_secrets_no_hardcoded_or_logged red (artifact docs/architecture/l5a-mutations/T1.json) |
| 16 | EV-016 | 2 | 0 | none | 154c3ff4e8f26edd | True | True | T2: M2d run_strict passes a missing usage line at exit 0: test_exit_zero_without_usage_raises red (artifact docs/architecture/l5a-mutations/T2.json) |
| 17 | EV-017 | 3 | 0 | none | c39bba679bab437d | True | True | T3: M3b resolve_write without the root prefix check: test_file_write_boundary red (artifact docs/architecture/l5a-mutations/T3.json) |
| 18 | EV-018 | 4 | 0 | none | 3140a12cc84da348 | True | True | T4: M4c a third party import with no DB scored: test_a_third_party_import_with_no_vulnerability_db_is_no_data red (artifact docs/architecture/l5a-mutations/T4.json) |
| 19 | EV-019 | 4 | 0 | none | 3701d9b4d9e2d990 | True | True | T5: M5 the replay hash unchecked: test_an_edited_output_block_is_caught_by_the_replay_hash red (artifact docs/architecture/l5a-mutations/T5.json) |
| 20 | EV-020 | 3 | 0 | none | 1c9427766c3c0539 | True | True | T6: M6 the unknown tool deny deleted: test_hostile_tool_calls_are_denied red (the M6 payload itself is limitation F-007) (artifact docs/architecture/l5a-mutations/T6.json) |
| 21 | EV-021 | 3 | 0 | none | 830e6503bdcdc76e | True | True | T7: M7 the grant owner check deleted: test_a_grant_owned_by_another_user_is_refused red (artifact docs/architecture/l5a-mutations/T7.json) |
| 22 | EV-022 | 2 | 0 | none | 5b792be6a28dd3ef | True | True | T8: M8 a non finite timeout passes _validate_job: test_hostile_timeout_is_refused red (artifact docs/architecture/l5a-mutations/T8.json) |
| 23 | EV-023 | 3 | 0 | none | be5ddd5bd44bed4a | True | True | T9: M9d the slots and claims directory guards deleted: test_slot_claim_owner red (artifact docs/architecture/l5a-mutations/T9.json) |

Findings passed to the procedure:

| id | point | control | status | target | evidence | note |
|---|---|---|---|---|---|---|
| F-001 | 1 | redactor | present | plugin/runtime/brother/core/or_fanout.py redact_text | EV-003 |  |
| F-002 | 2 | exec_gateway | present | plugin/runtime/brother/core/openrouter_strict.py run_strict | EV-004 | L5a-7: X8 a missing usage line on exit 0 raises FallbackDetected; X9 bridge_argv is type checked before the runner (EV-004 rows present) |
| F-003 | 2 | exec_gateway | present | plugin/runtime/brother/core/or_dispatch_cli.py main | EV-004 | L5a-7: X4 -- before the appended prompt; X11 a nonzero exit or a missing reported model returns 4 at the command entry (EV-004 rows present) |
| F-004 | 3 | path_resolver | present | plugin/runtime/brother/core/or_fanout.py run_job | EV-008 | L5a-8: every read and write under or_fanout requires a pinned root; EV-008 shows the unpinned read refused |
| F-005 | 3 | slot_owner | present | plugin/runtime/brother/core/dispatch_semaphore.py _checked_dir | EV-007 |  |
| F-006 | 3 | cap_grant | present | plugin/runtime/brother/core/openrouter_dispatch.py _effective_cap_grant | EV-009 | L5a-9: a grant open to others, owned by another user, or reached through a link never lifts the cap; EV-009 shows the 0644 grant refused |
| F-007 | 3 | hook | absent | scripts/brother_antigravity_hook.py handle_pre_tool | EV-010 | REQ-02 args gate not landed: EV-010 shows the spec's M6 payload allowed. Not in the L5a-7 to L5a-11 fix set; the owner ruled 2026-09-30 06:06 JST that the host is a trusted tool with full access (FX-49 retired, the host data guard opt in), and at 08:2x JST he ruled it out of L5a's scope (his words: Change of mind out of scope, I Trust antigravity, Go for option 1): a stated limitation, scoped out of point 3 in l5a6_score.SCOPED_OUT_CONTROLS |
| F-008 | 4 | check_deps | present | plugin/runtime/brother/core/or_dispatch_cli.py check_deps | EV-014 | L5a-10: the import census over the six inventory files answers point 4 from the tree (EV-014 carries the census line); pip-audit stays NO-DATA for a manifest that does not exist (EV-011, EV-012) |

F-006 carries the REQ-03 limitation, verbatim: "the same-user forger case is a documented limitation of point 3, not a defended attack: a process running as the owner's own OS user can write a grant with the right uid and modes, so no uid or mode check can tell it from the owner's own grant"

Per point output, as printed:

- point 1: verdict PASS, score 10.0
- point 2: verdict PASS, score 10.0
- point 3: verdict PASS, score 10.0; scoped out: hook (owner ruling 2026-09-30: Antigravity full access; F-007 is a stated limitation)
- point 4: verdict PASS, score 10.0
- overall mean: 10.0

Mutations T1 to T9 (acceptance threshold 10): MET. Rows EV-015 to EV-023
above each apply one section 10 mutation to a disposable copy of this tree
(scripts/l5a_mutation_probe.py, artifact under docs/architecture/l5a-mutations)
and quote the named test's red output; every row carries mutation_id and
mutation_artifact, which evidence_is_consistent requires, and every row is
re-run by the done check. The T1 test was found unable to see a planted
credential file (it read two named files only) and was fixed at the source
on 2026-09-30 before M1 could turn it red. The earlier adversarial sweep of
2026-09-30 at 9fb8a8b4f (25 mutants on python3 3.13, 14 on 3.9) left two
survivors, D2 and G2, both killed by the fix round (test_l5a11, test_l5a9c).

The procedure finding of the previous run (point 4 could never PASS) is
closed by L5a-10: point 4 is now scored on a census line the excerpt itself
carries, and this run scores it on EV-014.

Blocking: false. Every point is PASS at or above 8.5 and the overall mean is 10.0, so L5a passes. Stated limitation: F-007 (the hook allows the M6 payload, run_command with args.command) is out of point 3's scope by the owner's 2026-09-30 ruling under Antigravity full access; it is not a defended control.

L5a-9 closed F-006 for a different user: a grant readable or writable by others, owned by another user, or reached through a link never lifts the cap; the same user forger stays the documented limitation of REQ-03.
