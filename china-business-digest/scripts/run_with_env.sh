#!/bin/sh
# Export variables from a protected env file, then replace this shell with the command.
set -eu

if [ "$#" -lt 2 ]; then
    printf 'Usage: %s ENV_FILE COMMAND [ARG ...]\n' "$0" >&2
    exit 64
fi

env_file=$1
shift

# A relative operand to the POSIX dot builtin may otherwise be searched via PATH.
case $env_file in
    /*) ;;
    *) env_file=$PWD/$env_file ;;
esac

if [ -L "$env_file" ] || [ ! -f "$env_file" ]; then
    printf '%s\n' "Environment file must be a regular, non-symlink file: $env_file" >&2
    exit 66
fi

set -a
# The env file is intentionally sourced so its variables can be exported.
. "$env_file"
set +a

exec "$@"
