---
trigger: always_on
---

# Brother assurance rules

## Evidence before claims
- Never say a task is done, fixed or working unless a verifying command ran after the last edit. Name the command and quote its output.
- A check that cannot fail proves nothing. When a check guards a property, show it going red with the property removed.
- Missing evidence is NO-DATA. NO-DATA is never a pass.

## Bounded execution
- Change only the files the task names. A write outside the declared scope is reported, never hidden.
- Find where a defect originates before editing. Patching one caller while its siblings keep the defect is not a fix.

## Unknown input blocks
- Unknown, corrupt or unreadable input blocks. It never reads as the safe case.

## Memory is advisory
- Recalled context informs the work. Current evidence outranks it.
