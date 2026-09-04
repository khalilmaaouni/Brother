# 02. Process map

## Actors
The calling service, the Jobs API, the idempotency store, and the job runner.

## Steps
| # | Step | Actor | Trigger | Exception path |
|---|---|---|---|---|
| 1 | Caller sends POST /jobs with an Idempotency-Key header | Calling service | The caller wants work done | Header absent: the request is handled as today, no dedupe |
| 2 | Jobs API claims the key | Jobs API | A POST carrying a key arrives | Claim conflicts: the stored job id is returned, no job is created |
| 3 | Jobs API compares the request fingerprint | Jobs API | The key was already claimed | Fingerprint differs: 422 is returned naming the conflict |
| 4 | Job is created and the key is bound to its id | Jobs API | The claim succeeded | Create fails: the claim is released so a retry can win it |
| 5 | Job runner picks the job up | Job runner | A job row exists | Runner crashes: the job stays claimable, no key is consumed |

## Handoffs
| From | To | What is handed over | Contract |
|---|---|---|---|
| Calling service | Jobs API | An idempotency key plus the request body | The same key with the same body always returns the same job id |
| Jobs API | Idempotency store | A key claim | A claim is atomic; two concurrent claims produce exactly one winner |
| Jobs API | Job runner | A job row | A job id appears at most once for one key |
