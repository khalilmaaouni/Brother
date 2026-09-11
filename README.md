# Brother

**When AI says done, Brother gives you proof.**

Brother is an evidence and professional-practice layer for work another person may later have to trust. Describe the outcome. Brother decides whether the work is trivial enough to leave alone, substantial enough to need execution provenance, or risky enough to need stronger assurance. When Brother engages, it should leave a rerunnable receipt instead of another confident summary.

Brother is not a second project manager, a menu of agents, or an approval machine. It does not make a weak check strong. It does not turn remembered context into proof. It does not accept a result for you.

## Your first run, start to finish

```text reference
mkdir mathlib-toy && cd mathlib-toy
git init -q
git config --local user.name "Toy User"
git config --local user.email "toy@example.invalid"
cat > mathlib.py <<'EOF'
def add(a, b):
    return a + b
EOF
cat > test_mathlib.py <<'EOF'
import mathlib


def test_add_ints():
    assert mathlib.add(1, 2) == 3


def test_add_floats():
    assert mathlib.add(1.5, 2) == 3.5
EOF
git add mathlib.py test_mathlib.py
git commit -q -m "toy mathlib, before the fix"
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
/brother
no unfinished run found
Brother turns AI-assisted work into something checkable instead of just trusted. What are you trying to do right now: start or check on a project, or get a change proven safe before it ships?
make add() refuse non-numeric input with a clear error and cover it with a test
mathlib.py: check python3 -c "import mathlib; assert mathlib.add(1,2)==3 and mathlib.add(1.5,2)==3.5" && python3 -c "import mathlib; mathlib.add('a','b')" 2>&1 | grep -q '^TypeError: .' exited 0 (verified)
test_mathlib.py: check python3 -m pytest test_mathlib.py -q -k 'type or numeric or error or raise' exited 0 (NO-DATA: this check depends on guard, and its check was never re-run with that change reverted, so nothing shows the check exercises it)
brother_run: receipt: ~/.claude/brother-run/docs/plan/runs/20260903T071356-make-add-refuse-non-numeric-input-with-a/receipt/receipt.json
```

## Start in sixty seconds

### Claude Code

```bash reference
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

The marketplace this repository declares is named `brother`, in lower case, whatever case the repository slug you added it from carries. Use that spelling everywhere a command names the marketplace: `claude plugin marketplace update Brother` answers `Marketplace 'Brother' not found. Available marketplaces: brother` and exits 1.

Open a repository and invoke Brother. With no unfinished work, the bare door asks what you are trying to do rather than making you choose BrotherMode, BrotherSBE, or another internal product.

```text
/brother make add() reject non-numeric input and prove the behavior with a test
```

If unfinished Brother work exists in that repository, the door should discover it and offer/resume the plain-language outcome instead of exposing a run id as the user experience.

### Codex

Codex does not expose Brother through Claude's slash-command surface. Install the Brother plugin, wire the supported managed hooks/trust path, then use the installed Brother skill or runtime engine. See [Install on Codex](docs/how-to/install-codex.md).

To upgrade an existing install, remove the configured marketplace first, then add it again at the new ref, joined so no step runs over a failed one:

```bash reference
codex plugin marketplace remove brother && codex plugin marketplace add https://github.com/khalilmaaouni/Brother --ref <new ref> && codex plugin add brother@brother --json
```

## Check what you installed

BrotherMode and BrotherSBE ship `CHECKSUMS.sha256` and `verify-install.sh`. In either product directory, `bash scripts/verify-install.sh` re-hashes files and compares them with the shipped manifest, and changes nothing. Do not run `sh scripts/checksums.sh CHECKSUMS.sha256` first, because it rewrites the manifest from the current bytes and makes a tampered file agree with a fresh manifest. Regenerating the manifest is the maintainer's step when cutting a release, never the reader's step before verifying.

The shipped engine has `RUNTIME-MANIFEST.json`: run `python3 bundle/runtime/verify_runtime.py` from this repository, or `python3 runtime/verify_runtime.py` from an installed plugin root. It prints PASS, FAIL with differing files, or NO-DATA when the manifest is missing. Its manifest is the reference, so rewriting both a file and its manifest line passes, and comparison with the published release tag is a different check.

## One door, three outcomes

1. **Trivial and reversible:** Brother stays out of the way. If nobody would reasonably ask for evidence afterwards, trust ceremony is waste.
2. **Substantial change:** Brother routes to execution provenance: bounded work, isolated execution, checks, serial integration, receipt.
3. **Risk or decision-grade truth:** Brother adds assurance appropriate to the risk. Money, authentication, customer/partner data, migrations, production paths, and decision-grade figures should not inherit confidence from one green command.

Claim verification is an experimental product boundary unless the current public release explicitly says otherwise.

## Evidence vocabulary

| Verdict | Meaning |
| --- | --- |
| `PASS` | The named evidence supports the named claim. |
| `FAIL` | The named evidence contradicts the named claim. |
| `NO-DATA` | The available evidence did not establish either answer. |

`NO-DATA` is not a weak pass. It is correct when the check was irrelevant, already passed before the change, could not run, did not exercise its claimed dependency, or otherwise failed to discriminate the claim.

A useful receipt lets a reviewer answer: **what changed, what check ran, what did it actually discriminate, where did the expected result come from, where is the evidence, and what remains unproven?**

## A green check can prove nothing

A command exiting zero after a change is not automatically evidence for that change. A useful check must discriminate. Brother should expose cases such as:

- the check already passed before work;
- a behavior-changing unit has no recorded pre-implementation nonzero run of its own deciding check. That missing red-before-green witness is `NO-DATA`; documentation-only and generated units state their exemption;
- a unit changed no relevant file;
- a test still passes after the implementation it supposedly exercises is reverted;
- a dependency was assumed but never tested in the relevant state;
- evidence output cannot be recovered;
- a model-authored check is presented as independent review when it is not.

Run the check that holds this rule in place: `python3 scripts/test_brother_run.py`. It proves that an already-green, unchanged, or revert-insensitive check is reported as `NO-DATA`, not evidence.

## Work stays bounded

Substantial work is represented as bounded work units. A unit declares its objective, deciding command, allowed writes, and dependencies. Work that escapes its write set is not quietly accepted. Independent units can work separately; integration is serial because the final repository must have one truth.

Local unit integration is not the same as approving a pull request, publishing a release, deploying production, or making the human acceptance decision.

## Intake comes before planning when the work needs a contract

A plan says how to build. It does not prove the plan answers what the person asked.

The current outcome contract records the original question, language, success checks, affected products, required answers, evidence receipts, audit/ticket requirements, lifecycle state, history, and decision reference. The schema is `docs/schema/outcome-contract-v1.json`.

The profession pages in this repository are **professional lenses**, not current schema enum values. See [Outcome contract reference](docs/reference/outcome-contract.md).

## Human authority stays visible

Brother can gather evidence and organize review. Evidence can inform authority; evidence is not authority. When acceptance cites a run, it requires a review receipt or an explicit recorded review skip with its reason and decision maker. Human acceptance and release remain explicit decisions.

## Vault memory is context

The Vault can retain useful lessons, constraints, failed approaches, decisions, semantic definitions, incident symptoms, and test oracles. Recalled memory cannot overrule current code, current evidence, or an explicit human decision.

## Choose the shortest path

**Learn:** [Documentation home](docs/README.md) · [First verified change](docs/tutorials/first-verified-change.md) · [Why NO-DATA exists](docs/explanation/no-data.md)

**Do:** [Install Claude](docs/how-to/install-claude-code.md) · [Install Codex](docs/how-to/install-codex.md) · [Resume](docs/how-to/resume-work.md) · [Verify a migration](docs/how-to/verify-a-migration.md) · [Verify a number](docs/how-to/verify-a-number.md)

**Look up:** [Routing](docs/reference/routing.md) · [Outcome contract](docs/reference/outcome-contract.md) · [Verdicts](docs/reference/verdicts.md) · [Receipt](docs/reference/receipt-model.md) · [Work units](docs/reference/work-units.md) · [Hooks](docs/reference/hooks.md)

**Professional lenses:** [Senior backend](docs/personas/senior-backend-engineer.md) · [Senior data engineering](docs/personas/senior-data-engineer.md) · [Infrastructure/SRE](docs/personas/senior-infrastructure-engineer.md) · [Architect](docs/personas/architect.md) · [Data analyst](docs/personas/data-analyst.md) · [Data scientist](docs/personas/data-scientist.md) · [BA](docs/personas/business-analyst.md) · [Technical BA](docs/personas/technical-business-analyst.md) · [QA automation](docs/personas/qa-automation-engineer.md) · [Manual QA/QC](docs/personas/manual-qa-qc.md) · [Solo founder](docs/personas/solo-founder.md)

## Limits before adoption

- A receipt proves only the claims its evidence supports.
- A model-authored test is not automatically an independent oracle.
- Memory is not evidence.
- Tool exit success is not delivery proof.
- Claude Code and Codex have different host surfaces.
- Hook scope depends on install path/configuration; read [Hook scope](docs/reference/hooks.md).
- Tiny reversible tasks can still cost more through Brother than doing them directly, but not in every case any more: when a request already names its own existing file or files, and its own existing check written the one way Brother already knows how to run today (in the specific, narrow shape it already recognizes, not yet any test file in any framework), uses no risky wording, and the tree it runs against is already clean, Brother skips straight to doing the work and never opens a separate model session just to plan it, automatically, with nothing to turn on. Most everyday requests do not qualify, including one phrased only as plain instructions with no file or check named in it. Measured here with the model calls stood in by a script rather than a real one, so the figures below are Brother's own code and never a wait on a real model: four small requests were driven through the one command a person types, and all four finished successfully; the two that qualified opened no separate planning session at all, against one each for the two that did not, while Brother's own code took between 5.62 and 8.4 seconds either way. What a person waiting on a real model actually experiences from this is not recorded on this page.
- Exact version capability belongs in the public release and generated `SYSTEM.md`, not copied historical prose.

## Documentation is part of the evidence system

Current behavioral claims are registered in [DOC-CLAIMS.md](docs/assurance/DOC-CLAIMS.md). Public pages must not disagree silently.

**Tutorials teach. How-to guides solve tasks. Reference states the contract. Explanation gives reasoning. Persona pages apply the same evidence model to professional work.**

Start at [docs/README.md](docs/README.md).
