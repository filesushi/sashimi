#!/bin/sh
# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

set -eu

usage() {
  echo "usage: sh scripts/check.sh [full]" >&2
  echo "  (no argument)  ruff, mypy, and the tests on Python 3.14, printing only failures and the summary" >&2
  echo "  full           ruff, mypy, and the tests on every supported Python (3.11-3.14), verbose" >&2
}

MODE=quick
if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi
if [ "$#" -eq 1 ]; then
  case "$1" in
    full) MODE=full ;;
    *)
      echo "check.sh: unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENVS=${SASHIMI_CHECK_VENVS:-$ROOT/.venvs}
PYTHON_VERSIONS="3.11 3.12 3.13 3.14"
TOOLS_PYTHON=3.14

cd "$ROOT"
unset VIRTUAL_ENV

in_env() {
  version=$1
  shift
  UV_PROJECT_ENVIRONMENT="$VENVS/$version" uv run --frozen --python "$version" -- "$@"
}

quiet_tests() {
  status=$(mktemp)
  {
    code=0
    in_env "$TOOLS_PYTHON" pytest -q -o log_cli=false --tb=short 2>&1 || code=$?
    echo "$code" >"$status"
  } | grep -v -E '^[.sxXFE]+ +\[ *[0-9]+%\]$' || true
  code=$(cat "$status")
  rm -f "$status"
  return "$code"
}

echo "--- checking formatting with ruff ---"
in_env "$TOOLS_PYTHON" ruff format --check src/sashimi tests scripts

echo "--- linting with ruff ---"
in_env "$TOOLS_PYTHON" ruff check src/sashimi tests scripts

echo "--- type checking with mypy ---"
in_env "$TOOLS_PYTHON" mypy src/sashimi

if [ "$MODE" = quick ]; then
  echo ""
  echo "--- running tests with pytest on Python $TOOLS_PYTHON ($VENVS/$TOOLS_PYTHON), failures and summary only ---"
  quiet_tests
  echo ""
  echo "--- all checks passed on Python $TOOLS_PYTHON; run 'sh scripts/check.sh full' for every version ---"
  exit 0
fi

for version in $PYTHON_VERSIONS; do
  echo ""
  echo "--- running tests with pytest on Python $version ($VENVS/$version) ---"
  in_env "$version" pytest
done

echo ""
echo "--- all checks passed! ---"
