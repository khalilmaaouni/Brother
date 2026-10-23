#!/bin/sh
# scripts/install.sh: install Brother for one host into a home directory.
#
# Flag contract:
#   --home <path>            the home directory to install into (required)
#   --host <claude|codex>    which host configuration to install (required)
#   --uninstall              remove this host's marker instead of writing it
#
# The marker is <home>/<host>/.brother-installed; a successful install
# prints "installed <host>". This installer never invokes a model entry
# point (claude, codex, llm, aider, openai, anthropic), which is what the
# zero-model-call gate measures. Every argument is validated before any
# write: an unrecognized host or a missing --home refuses with a nonzero
# exit and leaves the tree untouched.
set -eu

home=""
host=""
uninstall=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --home)
            home="${2:-}"
            shift 2
            ;;
        --host)
            host="${2:-}"
            shift 2
            ;;
        --uninstall)
            uninstall=1
            shift
            ;;
        *)
            printf 'install: unknown argument: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if [ -z "$home" ]; then
    printf 'install: --home is required\n' >&2
    exit 2
fi

case "$host" in
    claude|codex) ;;
    *)
        printf 'install: unrecognized host: %s\n' "$host" >&2
        exit 2
        ;;
esac

target="$home/$host"

if [ "$uninstall" -eq 1 ]; then
    rm -f "$target/.brother-installed"
    exit 0
fi

mkdir -p "$target"
printf '%s\n' "$host" > "$target/.brother-installed"
printf 'installed %s\n' "$host"
exit 0
