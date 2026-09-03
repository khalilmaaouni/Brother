# Module: policy

LOAD WHEN: a registered check is being bound to a command, a policy rule is being evaluated, or the control plane is being changed.

(The check-severity contract, moved out of SKILL.md when the default profile was cut
down, plus the two control-plane files it governs. The routing table in
`references/modules.md` names when to load it.)

ENFORCEMENT: DECLARED BUT NOT ENFORCED. Nothing reads `.brothersbe/profile.json` for
this module. That is deliberate here rather than owed work: everything below enforces
itself in code at every profile, and a profile switch over it would be a way to
weaken a gate. This row exists to group a surface a reader can skip, and it says so.

## Severity, moved verbatim from SKILL.md

> Every registered check also declares its severity at write time, in its
> constructor: `gate` means a FAIL blocks a `--strict` run, `soft` means a FAIL is
> graded and blocks only under the opt-in `--strict-soft`. The severity prints on
> every verdict line, and `tools/sbe_checks.py` refuses to register a check that
> declares neither, the same way it refuses one whose empty state is PASS. Severity
> states only what a FAIL does to the exit code; it does not change what a check
> examines or reports, and it does not decide what a FAIL is worth reading: a soft
> FAIL is still a finding.

That contract is enforced in code whatever the profile is: `tools/sbe_checks.py`
raises at registration time, and no declaration in `.brothersbe/profile.json` can
reach it. Switching this module OFF does not weaken a gate. It only means the
session is not carrying the policy surface in context while it works on something
else.

## The surfaces

- `.sbe/checks.yml`: which command a check id actually is. A receipt minted with
  `sbe evidence run --check <id>` takes its executable, arguments, working directory,
  coverage and runner files from that file, and nothing on the command line can
  substitute any of them.
- `.sbe/policy.yml`: the rules evaluated over the change, including the
  control-plane rule that names `.sbe/checks.yml` itself, so editing the registry
  owes control-plane evidence and a protected approval.
- `src/brothersbe/policy.py` and `src/brothersbe/checks.py`: the evaluator and the
  binding.

## What this module does not decide

Nothing here waives a hard gate. The four gates in `tools/sbe_gate.py` and the
silent-failure lints are refused rather than waived at every profile, per L15 and
L16 in `references/laws-overrides-and-waivers.md`.
