#!/bin/sh
# SPDX-FileCopyrightText: 2026 The FileSushi Authors
# SPDX-License-Identifier: Apache-2.0

set -eu

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

echo "--- checking formatting with ruff ---"
in_env "$TOOLS_PYTHON" ruff format --check src/sashimi tests scripts

echo "--- linting with ruff ---"
in_env "$TOOLS_PYTHON" ruff check src/sashimi tests scripts

echo "--- type checking with mypy ---"
in_env "$TOOLS_PYTHON" mypy src/sashimi

for version in $PYTHON_VERSIONS; do
  echo ""
  echo "--- running tests with pytest on Python $version ($VENVS/$version) ---"
  in_env "$version" pytest
done

echo ""
echo "--- all checks passed! ---"
