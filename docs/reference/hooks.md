# Hook scope reference

Hook behavior depends on installation path. Do not repeat the obsolete blanket claim that repository opt-out does not exist.

## Product installer path

BrotherMode/BrotherSBE installer paths can scope hook activity to repositories the installer/user names. Repository configuration can opt supported scoped installs in/out using `.brother/config`:

```text
hooks: on
```

or:

```text
hooks: off
```

## Marketplace plugin path

Marketplace installation does not automatically run the product installer scripts, so do not assume it made the same repository-scope choice. Configure/verify the supported scope marker/config deliberately when repository-only behavior is required.

## Hooks everywhere

The product installer exposes a deliberate hooks-everywhere option. Treat it as explicit scope expansion, not an invisible default.

## Verify the installation

Installation success is not hook-scope proof. Inspect the marker/config and run the current installer diagnostics or hook-scope tests.

## Authority

Current product installers and hook-scope tests. This page is the canonical public prose for scope.
