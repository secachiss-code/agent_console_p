#!/bin/sh
# Tools named by hostrun check. Each name is printed. Exit 1 if any tool fails.
set -u
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
py="$root/.venv-core/bin/python"
fail=0

run() {
  printf '%s\n' "$1"
  shift
  if ! "$@"; then
    fail=1
  fi
}

run ruff "$root/.venv-core/bin/ruff" check "$root"
run basedpyright "$root/.venv-core/bin/basedpyright"
run import-linter "$root/.venv-core/bin/lint-imports" --config "$root/pyproject.toml"
run check_no_js "$py" "$root/tools/check_no_js.py" "$root"
run check_no_raw_sql "$py" "$root/tools/check_no_raw_sql.py" "$root/src" "$root/tests"
run contrast_check "$py" "$root/tools/contrast_check.py"
run check_contracts "$py" "$root/tools/check_contracts.py" "$root"
run api_floor_check "$py" "$root/tools/api_floor_check.py" "$root/src"
printf '%s\n' api_floor_fixture
if "$py" "$root/tools/api_floor_check.py" "$root/tests/fixtures/ui_banned"; then
  printf 'api_floor_fixture: Adw.Spinner was not rejected\n' >&2
  fail=1
fi

container="${HOSTRUN_CONTAINER:-}"
if [ -n "$container" ]; then
  run ownership "$py" "$root/tools/check_ownership.py" "$root" "$container"
fi

exit "$fail"
