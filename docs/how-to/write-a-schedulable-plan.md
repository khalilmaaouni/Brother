# Write a schedulable plan

Use this for the advanced/in-session route where the current session supplies work units.

1. Use 2 to 9 bounded units for the current route.
2. Run each proposed `done_check` before editing; if it is already green, rewrite it.
3. Declare every file each unit may write.
4. Use `deps` only for real ordering and valid unit ids.
5. Do not run two units writing the same path in the same wave.
6. Keep the plan JSON outside the target repository.
7. Pair the plan with the outcome contract when the current route requires it.

## Verify the result

Run current engine plan validation before editing. Bad plans should fail before claiming work/opening partial delivery.
