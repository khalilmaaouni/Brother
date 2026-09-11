# Why NO-DATA exists

Binary dashboards encourage a fiction: if nothing is red, everything is green.

`NO-DATA` is for evidence that never established the answer.

A bug-fix test passes after the fix. If it also passed before the fix, the green result says nothing about whether the fix caused the desired behavior. Calling it PASS rewards the appearance of testing instead of discrimination.

The same applies to a dashboard number with no independent reconciliation, an infrastructure plan that could not read part of live state, an untested rollback, a scanner that skipped a directory, or an unmeasured Vault-impact claim.

`NO-DATA` preserves the difference between “we looked and found no contradiction” and “we could not establish the claim.” The next action is usually better evidence, a narrower claim, or explicit human acceptance of uncertainty.
