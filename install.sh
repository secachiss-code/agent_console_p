#!/bin/sh
# Install the two locked interpreter profiles without privilege escalation.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
HOME_DIR=$(CDPATH= cd -- "$HOME" && pwd -P)
allow_outside=0
dry_run=0
for arg in "$@"; do
    case "$arg" in
        --allow-outside-home) allow_outside=1 ;;
        --dry-run) dry_run=1 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
case "$ROOT/" in
    "$HOME_DIR/"*) ;;
    *) if [ "$allow_outside" -ne 1 ]; then
        printf 'Checkout is outside HOME; pass --allow-outside-home to continue.\n' >&2
        exit 1
    fi ;;
esac
printf 'GTK dependencies must be installed by the user; see project documentation.\n'
if [ "$dry_run" -eq 1 ]; then
    printf 'Would sync %s/.venv-core and %s/.venv-ui from uv.lock.\n' "$ROOT" "$ROOT"
    exit 0
fi
UV=${ACONSOLE_UV:-uv}
cd "$ROOT"
UV_PROJECT_ENVIRONMENT="$ROOT/.venv-core" "$UV" sync --locked --no-python-downloads --python 3.15t
UV_PROJECT_ENVIRONMENT="$ROOT/.venv-ui" "$UV" sync --locked --group ui --no-python-downloads --python 3.15
