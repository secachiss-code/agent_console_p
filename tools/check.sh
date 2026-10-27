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
ui_path() {
  case "$1" in
    tests/ui/test_spike.py) return 1 ;;
    src/aconsole/ui/viewmodels/*|src/aconsole/ui/views/*|src/aconsole/ui/style/*|\
    src/aconsole/ui/resources/*|src/aconsole/ui/po/*|src/aconsole/ui/AGENTS.md|\
    src/aconsole/__main__.py|tests/ui/*|tests/gui/front/*|docs/ui/B[0-9]/*|docs/ui/B[0-9][0-9]/*|\
    stubs/*|tools/ui_snapshot.py|docs/plan/front/execplans/*|docs/contracts/requests/*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ -n "$container" ]; then
  printf '%s\n' ownership
  for path in $(git -C "$root" diff --name-only main...HEAD); do
    if [ "$container" = "agent-codex" ]; then
      if ! ui_path "$path"; then
        printf 'ownership: %s\n' "$path" >&2
        fail=1
      fi
    elif ui_path "$path"; then
      printf 'ownership: %s\n' "$path" >&2
      fail=1
    fi
  done
fi

exit "$fail"
