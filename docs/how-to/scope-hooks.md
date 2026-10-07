# Scope or pause Brother hooks

Understand the install path first. Product installers can establish repository-scoped behavior; marketplace install does not automatically make the same scoping decision.

For supported scoped installs, edit `.brother/config` in the repository. To enable hooks, use:

```text
hooks: on
```

To pause reporting and advisory hooks, use:

```text
hooks: off
```

Write guards remain active in an opted-in repository. `hooks: off` does not disable them. See [BrotherMode's hook behavior](../../products/brothermode/README.md#hooks-scope-and-limits) for the limits.

Use hooks-everywhere only as an intentional scope expansion.

## Verify the result

Test an opted-in repository and an unrelated repository with current diagnostics/tests. Do not infer scope from installation success.

See [Hook scope](../reference/hooks.md).
