# 07. Verification plan

| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| The same key with the same body returns the same job id | Integration test posting one key twice and asserting one job row | Every pull request |
| Two concurrent claims produce exactly one job | Concurrency test firing 50 parallel POSTs on one key | Every pull request |
| A key reused with a different body is rejected | Integration test asserting a 422 and no new job row | Every pull request |
| Expired keys are deleted | Reconciliation query counting keys older than 72 hours | Daily |
