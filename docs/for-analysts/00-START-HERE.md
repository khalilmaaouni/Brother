# Start here if you define success

The painful moment is hearing that work is done while your original requirement has disappeared into technical language.

## What Brother gives you

You state what must be true in plain words. Brother keeps those words visible through the run and returns PASS, FAIL, or NO-DATA with a receipt.

PASS means the named evidence supports the requirement. FAIL means it contradicts the requirement. NO-DATA means the evidence did not establish an answer. It is not a quiet pass.

The acceptance screen is still yours. Brother does not accept the result for you.

## How the benefit is checked

The developer can run these commands from the repository root:

```bash
python3 scripts/test_receipt_door.py
python3 scripts/test_fable_authority.py
```

The first checks that a passing command cannot claim more than it proved. The second checks that acceptance stays a human decision.

## What the Vault changes

The Vault keeps approved lessons, constraints, and decisions in local Markdown. A later task can see a relevant lesson before work reaches the same area. The lesson is context, not truth. Current evidence and your current intent win.

The developer can prove recall with:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
```

## Limits

You still need a developer to turn a requirement into a useful check. A weak check can still miss the behavior you care about. The public example has no recorded human acceptance, and the effect of recalled lessons on repeated mistakes has not yet been measured.

Read [DAY-ONE.md](DAY-ONE.md) for the shortest path, then [A-WORKED-EXAMPLE.md](A-WORKED-EXAMPLE.md) for one customer problem from requirement to receipt.
