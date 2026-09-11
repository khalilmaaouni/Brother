# Scope or disable Brother hooks

Understand the install path first. Product installers can establish repository-scoped behavior; marketplace install does not automatically make the same scoping decision.

For supported scoped installs, `.brother/config` can contain:

```text
hooks: on
```

or:

```text
hooks: off
```

Use hooks-everywhere only as an intentional scope expansion.

## Verify the result

Test an opted-in repository and an unrelated repository with current diagnostics/tests. Do not infer scope from installation success.

See [Hook scope](../reference/hooks.md).
