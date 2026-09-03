# Operator model

Notes on how the operator works, what they value, what they have corrected. Kept
so the skill stops relearning the same preference every week.

## Prediction ledger

Seal a prediction BEFORE you make a recommendation, so calibration is measurable.
prediction-audit counts rows that start with a real 20xx date and carry five
pipe-separated fields; sealed when the third field is a date (not n/a), scored
when the outcome starts with yes/hit/no/miss. The placeholder row is not counted.

Columns: date | claim | sealed-on | confidence | outcome

YYYY-MM-DD | <the falsifiable claim> | YYYY-MM-DD | <low/med/high> | <pending, then yes/no>
