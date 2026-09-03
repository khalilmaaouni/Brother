# BrotherSBE: setup

This page documents the original manual path: cloning the skill by hand and wiring the hooks yourself. It is still supported, but it is not the default. Three paths are documented in [README.md](../README.md): the marketplace pair (recommended for one person or a small team), `sh install.sh` (one command, works on any host, and applies your team's committed profile), and the tag-pinned, checksum-verified path in [docs/ROLLOUT.md](ROLLOUT.md) (for an organization rolling this out across many repositories). Come here when you want to inspect or hand-place every file yourself, or when [docs/MIGRATION.md](MIGRATION.md) sends you here while moving between paths.

Install is a few minutes. Turning the gates from advisory into blocking is real CI work, and this document is honest about which is which.

## Prerequisites

- Claude Code (BrotherSBE is a Claude Code skill).
- Python 3 on PATH (the tools use the standard library only, no third-party packages, no network).
- Git (the approval gate reads commit trailers and signatures).

## 1. Clone the skill

```
git clone https://github.com/khalilmaaouni/BrotherSBE ~/.claude/skills/brothersbe
```

Standalone: it works with nothing else installed. See [PARITY.md](../PARITY.md) for what it shares with BrotherModeUp.

## 2. Point the vault at a folder you choose

```
export BROTHERSBE_VAULT="$HOME/BrotherSBEVault"   # put this in your shell profile
```

Everything the skill writes (telemetry, correction candidates, session logs) goes there, and nowhere else. Copy the starter memory from `memory-template/` into that folder the first time.

## 3. Wire the hooks

Into `~/.claude/settings.json`, or a project `.claude/settings.json`. The harness fires these, not the model, which is the point: the save-before-you-die rule cannot be run by the actor that is dying.

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command",
        "command": "python3 ~/.claude/skills/brothersbe/tools/sbe_sessionstart.py"}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command",
        "command": "python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py outcomes-append"}]}
    ],
    "PreCompact": [
      {"hooks": [
        {"type": "command",
         "command": "python3 ~/.claude/skills/brothersbe/tools/sbe_autosave.py precompact",
         "timeout": 120},
        {"type": "command",
         "command": "python3 ~/.claude/skills/brothersbe/tools/sbe_telemetry.py precompact-brief"}
      ]}
    ]
  }
}
```

What each does: SessionStart injects the active-laws digest plus mechanical nags. SessionEnd captures nothing by default: each category is separately opt-in through `BROTHERSBE_TELEMETRY_METRICS`, `BROTHERSBE_TELEMETRY_TRANSCRIPT` and `BROTHERSBE_TELEMETRY_CORRECTIONS`, and `BROTHERSBE_TELEMETRY_DISABLE` forces all of them off for an organization that wants no choice in the matter. A category that stays off names the switch that kept it off. The data dictionary is in [SECURITY.md](../SECURITY.md). PreCompact does two things: it snapshots the whole worktree to a private git ref so a token-death is recoverable, and it writes the brief that survives the compaction. Both commands are in the block above; a setup carrying only the first loses the brief. Every hook exits 0 and never blocks a session. Opt-outs are in [SECURITY.md](../SECURITY.md).

## 4. Prove it works, in 60 seconds

Section 1 cloned the repo and left you wherever you were, so enter the clone first;
every command from here down is relative to it.

```
cd ~/.claude/skills/brothersbe
```

The eval bed and the honesty meta-test are documented once, with the real verbatim
output, in [README.md](ENGINEERING-REFERENCE.md#a-60-second-first-run): run them from this
directory. Then see the gates on a directory:

```
python3 tools/sbe_gate.py .            # all four gates, advisory
python3 tools/sbe_gate.py numbers .    # one class
python3 tools/sbe_gate.py --strict design   # enforcing: exits nonzero on any FAIL
```

## 5. Turn the gates from advisory into blocking (the real step)

Cloning the skill gives you the tools. It does not stop a bad merge until you wire `--strict` into the CI of the repository you want guarded. This is the same CI wiring [README.md](ENGINEERING-REFERENCE.md#wire-the-checks-into-ci-every-install-path) documents in full, with the actual workflow steps kept in one place so a step added there is never silently missing here: copy [`.github/workflows/brothersbe-gates.yml`](../.github/workflows/brothersbe-gates.yml) into the guarded repo (and make `tools/` reachable there, by vendoring it or adding a clone step). It runs on every pull request, seven steps, not three: the first blocks on a failed hard gate (a number with no re-run, an untested migration reverse, an unsigned money-path change, an unrun check). The second blocks on an incomplete dossier (a missing artifact, an ADR with no rejected alternatives, an entity with no system of record, a diagram node nothing defines, a dossier that is still the shipped template). The third blocks on a silent-failure lint. Three more run the regression evals, the honesty meta-test and the tool tests, because a gate whose fixtures nobody runs is a gate nobody knows still works. The waiver step (third of the seven) surfaces any design waiver as an annotation and in the job summary, because a waiver examined nothing and the exit code cannot tell you it happened. Advisory mode tells a session; only this CI wiring stops a merge, and that is by design.

Two settings decide whether those steps can see anything.

**`SBE_DOSSIER_ROOT`.** The design step is given the checkout root, and from there it walks for every directory holding a `00-intake.json` or any of `01` through `07`, which is what lets it reach a dossier in `design/<project>/` and what stops a deleted intake file from hiding one. Left empty, finding none is NO-DATA and the step passes, because a change that needs no dossier should not be blocked for not having one, and a T0 change needs none. Set it to where your dossiers live once the repository is supposed to carry one, and a declared root holding none becomes a FAIL. One caveat worth knowing before you set it: a repository that mixes T0 work with dossier work should leave it empty, because a declared root plus a legitimately dossier-free change is a FAIL by design. A directory holding dossier-shaped files that are not live design work (a template library, a finished project) carries a `.sbe-exempt` file whose contents say why, printed on every run:

```yaml
env:
  SBE_DOSSIER_ROOT: design
```

**Signer keys, for the approval gate.** The gate accepts a signed `Approved-by:` trailer only if the host running it actually verified the signature. A stock runner has no public keys imported, so `git` reports that it cannot check the signature, and the gate calls that NO-DATA rather than an approval. That is deliberate: a gate that accepted an unverifiable signature would trust a key nobody on the team recognises while rejecting a known key that had merely expired. Two working configurations:

```yaml
# either import the approvers' public keys into the job
- run: gpg --import <<< "${{ secrets.SBE_APPROVER_PUBKEYS }}"
# or use the keyless path, a Reviewed-in: <review id> trailer on the commit
```

Doing neither is legal and honest: approvals then report NO-DATA in CI, and the binding is enforced wherever your review platform enforces it. Note which of the two paths you are on. The signature path is forgery-resistant: an agent without the private key cannot produce it. The `Reviewed-in:` path is not, because nothing resolves the id and the agent writes the commit message. The gate says so in its own evidence on every run. If you want that path to be a control rather than a pointer, add a step that queries your review platform for the id and fails when it does not exist.

## What you get, and what you do not

You get a colleague that arrives with its checks already run and is bound by law to write UNVERIFIED next to anything that has not cleared them (the label is the agent's to write; no tool applies it), plus a memory that improves through reviewed pull requests. You do not get autonomy, an oracle, or enforcement without the CI step above. Those omissions are the point: see [DESIGN.md](DESIGN.md), "7. The register, and what it refuses".
