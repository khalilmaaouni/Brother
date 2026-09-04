# Adopting it on a team

The failure mode is ceremony: a dossier per pull request, a green check nobody
reads, and a linter people learn to route around. The design has specific
defences against that, and specific weaknesses. Both are below.

## The one thing that makes it real

Nothing blocks until you wire `--strict` into the CI of the repository you want
guarded. Cloning the skill gives you scripts. Local runs print verdicts and exit
0.

A ready workflow ships at `.github/workflows/brothersbe-gates.yml`. Copy it into
the guarded repository and make `tools/` reachable there, by vendoring it or by
adding a clone step.

## What to put in CI

Seven steps in the shipped workflow. Here is what each one buys and what it costs.

| Step | Blocks on | Put it in on day one? |
|---|---|---|
| `sbe_gate.py --strict design` | A figure with no re-derivation, an untested migration reverse, an unbound approval, an unrun check | Yes. Absent evidence is `NO-DATA` and does not block, so it taxes nobody. |
| `sbe_design.py --strict .` | An incomplete dossier | Only after you have decided where dossiers live. See the trap below. |
| Surface design waivers | Nothing. It annotates | Yes, if you run the design step. |
| `sbe_score.py --strict --strict-soft .` | Silent-failure lints | Yes for the gate lint. `--strict-soft` also blocks on the graded telemetry checks, which report `NO-DATA` without a vault, so it is harmless until you have one. |
| `evals/run_evals.py` | A check that stopped catching its defect | Yes. It was 509 cases on the run in `00-READ-ME-FIRST.md` and takes seconds. |
| `evals/test_no_data_class.py` | A check that would PASS over hollow evidence | Yes. |
| `tools/test_sbe.py` | Redaction, permissions, identity, autosave | Yes. |

The last three are the ones teams skip, and they are the reason the first three
are worth anything. A fixture no merge runs is documentation, not a gate.

Two settings decide whether the steps can see anything:

- **`SBE_DOSSIER_ROOT`.** Left unset, finding no dossier is `NO-DATA` and the step
  passes, which is correct for a repository that mixes small changes with design
  work. Set it, and a run finding no dossier becomes a FAIL. Set it only when the
  repository is genuinely supposed to carry one on every change. Also see
  `20-what-it-will-not-tell-you.md`: the variable replaces the directory argument,
  so keep it in the CI job and out of anyone's shell profile.
- **Signer keys.** The approval gate PASSes only on a signature this host verified
  against a key it trusts. A stock runner has none, so approvals report `NO-DATA`.
  Import the approvers' public keys, or accept `NO-DATA` and enforce review on
  your platform. Pick one and write down which.

The checkout step needs `fetch-depth: 0`, because the approval gate reads commit
trailers and signatures.

## What to leave manual

- **Deciding a change needs an approval.** No tool detects this.
- **Reading whether a rejected alternative is real.** The tool counts them; a
  person judges them. Its own PASS line says so.
- **The dossier for T0 work.** T0 owes nothing. Most changes are T0. If your team
  is writing dossiers for one-line fixes, the tier is being overridden by habit,
  not computed.
- **Whether a `Reviewed-in:` id resolves.** Unless you add the CI step that
  queries your platform, in which case it becomes automatic and you should.
- **Waivers.** A `.sbe-exempt` prints as `WAIVED` with its stated reason on every
  run. That is a human control, and it only works if a human reads the annotation.

## The tier is what keeps it from becoming ceremony

Five questions, first matching rule wins:

- **T3** (money, partner data, personal data, production state, or not reversible
  in an hour): all seven artifacts.
- **T2** (a contract change, or many consumers): six.
- **T1** (one boundary crossed, or some consumers): the purpose brief only.
- **T0**: nothing.

This is the whole anti-ceremony mechanism, and it works only if people run the
intake honestly rather than answering to reach a tier. Overriding is possible and
deliberately annoying: you must edit three fields in `00-intake.json` (`tier`,
`override`, `override_reason`, the reason at least 3 words and 12 characters), and
setting one without the others FAILs the design check as an edit rather than an
override. The intake prints those instructions on every run.

Worth checking in your first month: what fraction of merged pull requests
computed T0. If it is not most of them, something is being answered wrong.

## How the team's own rules accumulate

There is one file that travels between installs: `memory-template/LEARNED.md`.
Three lines per entry, and the format is fixed:

```
LESSON: <what went wrong or what was learned, one line>
RULE:   <the specific, checkable thing to do or not do>
BECAUSE: <the concrete cost that justifies the rule>
```

A lesson becomes a team law only when a pull request adding those three lines is
reviewed and merged. Every install reads the file at session start. The governance
property that matters: **no colleague's tool changes behaviour silently.** A rule
you did not agree to cannot arrive on your machine without a diff you could have
read.

Everything below that line stays local. Telemetry, correction candidates and
session logs live in `$BROTHERSBE_VAULT` on the machine that produced them, and no
CI step reads them. The observation loop informs; the promotion loop decides, and
it runs through code review like everything else.

The practical cadence: an engineer hits the same wall twice, proposes the three
lines, a teammate judges the rule rather than the incident, and it merges. If the
rule cannot be written as something checkable, it is advice, and the project keeps
advice in a separate file that says so.

## Failure modes of adoption, visible in the design

**The design step FAILs on every unrelated change.** Caused by setting
`SBE_DOSSIER_ROOT` in a repository that mixes T0 work with design work. A declared
root plus a legitimately dossier-free change is a FAIL by design. Leave it unset
until that stops being true. This is the fastest way to get the whole workflow
deleted.

**A stale dossier blocks merges forever.** A finished project's dossier keeps
being checked. The escape is a `.sbe-exempt` file whose contents say why, which
prints as `WAIVED` on every run. The escape exists precisely so nobody switches
the gate off instead. Use it, and read the annotations.

**The linter fires on vendored code.** Directories are pruned by markers inside
them (`pyvenv.cfg`, `.dist-info`, `CACHEDIR.TAG`, a git object store, a
`node_modules` carrying package metadata), never by name alone. A hand-vendored
tree carrying no marker **is** scanned. If you vendor without a marker, expect
hits and either add the marker or exempt the lines.

**Waivers become the workflow.** A scan whose every finding was waived reports
`NO-DATA` rather than clean, and a bare `# sbe: allow-silent` with no reason waives
nothing. Those two rules do a lot of work. What they cannot do is judge whether the
reason you wrote is a good one. That is the reviewer's job, and if nobody reads the
reasons the control is gone.

**`NO-DATA` gets read as a pass.** Every tool prints the banner "NO-DATA is never a
pass" on every run, and `--strict` does not exit nonzero on it. Both are correct: a
change with nothing to prove should not be taxed. The consequence is that a change
that *should* have emitted a receipt and did not looks identical to one that owed
nothing. Nothing in the tool closes that gap. If it matters for a given path, add a
CI step asserting the receipt exists where that change should have written it.

**Green in a monorepo means less than it looks.** The hard gates sum evidence
across the whole tree into one line per gate. The scope clause on each verdict
names the receipt files it read and lists the directories that produced none, so
the summing is legible rather than hidden, but it is still one verdict. Run the
gates against the directory the change owns, and read the scope clause. See
`20-what-it-will-not-tell-you.md`, item 2.

**Nobody protects the workflow file.** No CODEOWNERS and no branch protection
ships. Whoever can edit `.github/workflows/` can delete the gates. That is your
repository's setting, and it is worth doing on the same day you add the workflow.

## A sensible first two weeks

1. Everyone runs `evals/run_evals.py` once, so the trust claim is executable
   rather than asserted.
2. Wire the hard gates, the lints, and the three test suites into CI. Leave
   `SBE_DOSSIER_ROOT` unset. Nothing new blocks, because absent evidence is
   `NO-DATA`.
3. Pick one real T2 or T3 change and write its dossier. One. See whether the
   checks caught anything a person would have missed.
4. Decide the approval path: import signer keys, or accept `NO-DATA` and enforce
   review on your platform. Write the choice down.
5. Only then set `SBE_DOSSIER_ROOT`, and only in a repository where every change
   is supposed to carry a dossier.
6. Promote the first `LEARNED.md` rule the day someone hits the same wall twice,
   not before.
