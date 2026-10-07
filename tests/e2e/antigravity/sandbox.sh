#!/bin/sh
# L1b.1 sandbox provisioning. Standard library shell only.
set -eu

usage() {
  echo "usage: sandbox.sh create SOURCE_PLUGIN [--allow-repo]" >&2
  echo "       sandbox.sh cleanup SANDBOX_PATH" >&2
  exit 2
}

absolute_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$(pwd)" "$1" ;;
  esac
}

canonical_dir() {
  d="$1"
  [ -d "$d" ] || return 1
  ( cd "$d" && pwd -P )
}

repo_root() {
  if [ "${BROTHER_E2E_REPO_ROOT:-}" != "" ]; then
    raw="$BROTHER_E2E_REPO_ROOT"
    required="1"
  elif git rev-parse --show-toplevel >/dev/null 2>&1; then
    raw="$(git rev-parse --show-toplevel)"
    required=""
  else
    return 0
  fi
  if [ -z "$raw" ]; then
    return 0
  fi
  if ! resolved="$(canonical_dir "$raw")"; then
    if [ "$required" = "1" ]; then
      return 1
    fi
    return 0
  fi
  printf '%s\n' "$resolved"
}

is_inside() {
  case "$1" in
    "$2"|"$2"/*) return 0 ;;
    *) return 1 ;;
  esac
}

refuse_source() {
  source_plugin="$1"
  if [ ! -d "$source_plugin" ]; then
    echo "sandbox: source plugin missing or not a directory: $source_plugin" >&2
    exit 1
  fi
  if ! resolved="$(canonical_dir "$source_plugin")"; then
    echo "sandbox: cannot resolve source plugin: $source_plugin" >&2
    exit 1
  fi
  if [ -z "$resolved" ] || [ "$resolved" = "/" ]; then
    echo "sandbox: refusing unsafe source plugin: $source_plugin" >&2
    exit 1
  fi
  printf '%s\n' "$resolved"
}

create_sandbox() {
  source_plugin_input="$1"
  allow_repo="${2:-}"

  source_plugin="$(refuse_source "$source_plugin_input")"

  root=""
  if ! root="$(repo_root)"; then
    echo "sandbox: refusing repo target, BROTHER_E2E_REPO_ROOT cannot be resolved: ${BROTHER_E2E_REPO_ROOT:-}" >&2
    exit 1
  fi

  if [ "${BROTHER_E2E_SANDBOX_TARGET:-}" != "" ]; then
    requested="$(absolute_path "$BROTHER_E2E_SANDBOX_TARGET")"
    case "$requested" in
      ""|"/") echo "sandbox: refusing unsafe target: $requested" >&2; exit 1 ;;
    esac
    parent="$(dirname "$requested")"
    if [ ! -d "$parent" ]; then
      echo "sandbox: target parent missing or not a directory: $parent" >&2
      exit 1
    fi
    if ! parent_resolved="$(canonical_dir "$parent")"; then
      echo "sandbox: cannot resolve target parent: $parent" >&2
      exit 1
    fi
    target="$parent_resolved/$(basename "$requested")"
    if [ -e "$target" ] && [ ! -d "$target" ]; then
      echo "sandbox: target exists and is not a directory: $target" >&2
      exit 1
    fi
  else
    target="$(mktemp -d "${TMPDIR:-/tmp}/brother-e2e-antigravity.XXXXXX")" || exit 1
  fi

  if [ "$allow_repo" != "1" ] && [ "${BROTHER_E2E_ALLOW_REPO:-}" != "1" ] && [ "$root" != "" ] && is_inside "$target" "$root"; then
    echo "sandbox: refusing repo target $target (set BROTHER_E2E_ALLOW_REPO=1 or pass --allow-repo)" >&2
    exit 1
  fi

  if [ -e "$target" ] && [ ! -d "$target" ]; then
    echo "sandbox: target exists and is not a directory: $target" >&2
    exit 1
  fi
  mkdir -p "$target" || exit 1
  marker="$target/.brother_e2e_sandbox"
  printf '%s\n' "$source_plugin" > "$marker" || exit 1
  dest="$target/plugin"
  if [ -e "$dest" ]; then
    echo "sandbox: destination already exists: $dest" >&2
    exit 1
  fi
  if ! cp -RL "$source_plugin" "$dest"; then
    echo "sandbox: cp -RL failed for $source_plugin" >&2
    rm -rf "$target"
    exit 1
  fi
  printf '%s\n' "$target"
}

cleanup_sandbox() {
  target_raw="$1"
  case "$target_raw" in
    ""|"/") echo "cleanup: refusing unsafe path: $target_raw" >&2; exit 1 ;;
  esac
  if [ ! -d "$target_raw" ]; then
    echo "cleanup: sandbox path missing or not a directory: $target_raw" >&2
    exit 1
  fi
  if ! resolved_cleanup="$(canonical_dir "$target_raw")"; then
    echo "cleanup: cannot resolve sandbox path: $target_raw" >&2
    exit 1
  fi
  if [ -z "$resolved_cleanup" ] || [ "$resolved_cleanup" = "/" ]; then
    echo "cleanup: refusing unsafe path: $target_raw" >&2
    exit 1
  fi
  target="$resolved_cleanup"
  marker="$target/.brother_e2e_sandbox"
  if [ ! -f "$marker" ]; then
    echo "cleanup: refusing path outside the scratch root: $target" >&2
    exit 1
  fi
  rm -rf "$target"
}

case "${1:-}" in
  create)
    shift
    if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then usage; fi
    source_plugin="$1"
    allow=""
    if [ "$#" -eq 2 ]; then
      case "$2" in
        --allow-repo) allow="1" ;;
        *) usage ;;
      esac
    fi
    create_sandbox "$source_plugin" "$allow"
    ;;
  cleanup)
    shift
    if [ "$#" -ne 1 ]; then usage; fi
    cleanup_sandbox "$1"
    ;;
  *)
    usage
    ;;
esac
