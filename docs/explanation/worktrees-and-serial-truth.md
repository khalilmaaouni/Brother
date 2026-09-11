# Workers parallel, truth serial

Parallel execution helps only if the final repository still has one coherent state.

Brother isolates independent work in worktrees and uses declared write sets/dependencies to distinguish real independence from collisions. But integration is serial: each unit meets the repository state that actually exists when it lands.

Concurrency is an execution optimization. The integrated tree is shared truth.

This also improves review: a receipt can associate bounded work with the files it was allowed to touch and the check that decided it. Surprise writes become visible instead of disappearing inside a large agent diff.

See [Work units](../reference/work-units.md).
