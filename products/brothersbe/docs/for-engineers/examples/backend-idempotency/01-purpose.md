# 01. Purpose brief

## Problem
The internal Jobs API creates a job on every POST. A client that retries after a
timeout creates a second job, and the two jobs then run the same work twice.
Duplicate jobs have caused double-sent operator emails and duplicate export files
on the shared drive.

## Users
Platform teams calling the Jobs API from their own services. Today they guard
against duplicates by writing their own dedupe table keyed on a request id, and
three teams have written three different versions of that table.

## Success criteria
A POST carrying the same idempotency key returns the same job id and creates no
second job. A retry storm produces one job. A key reused with a different request
body is rejected rather than silently ignored.

## Non-goals
This does not change job scheduling, does not change the job result format, and
does not add idempotency to the job cancel path.

## What breaks if this is wrong
Callers that rely on the returned job id would get a job id belonging to a
different request, and two teams would act on one job while believing they own
two. Wrong reuse is worse than no idempotency at all.
