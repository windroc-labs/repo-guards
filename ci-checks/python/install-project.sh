#!/usr/bin/env bash
# Usage: install-project.sh RESOLVED_OUT, run in the project's directory.
# Installs the project into .venv as it is: a uv project from uv.lock, else the files in REQUIREMENTS (space-separated)
# and, when present, REQUIREMENTS_DEV, resolved now. RESOLVED_OUT gets that resolution, with each package's index.
# Only the packages in UV_NO_BINARY_PACKAGE build from source: uv sync reads it with UV_NO_BUILD, uv pip reads neither.
set -euo pipefail
resolved=$1

if [[ -f uv.lock ]]; then
  uv sync --locked
  exit 0
fi

files=()
read -ra required <<< "${REQUIREMENTS:-requirements.txt}"
for file in "${required[@]}"; do
  files+=("$file")
done
read -ra dev <<< "${REQUIREMENTS_DEV:-}"
for file in "${dev[@]}"; do
  if [[ -f $file ]]; then
    files+=("$file")
  fi
done

if [[ -f .python-version ]]; then
  uv venv --clear .venv
else
  uv venv --clear --python 3.12 .venv
fi
wheels=(--only-binary :all:)
read -ra built <<< "${UV_NO_BINARY_PACKAGE:-}"
for name in "${built[@]}"; do
  wheels+=(--no-binary "$name")
done
uv pip compile --python .venv/bin/python "${wheels[@]}" --emit-index-annotation --generate-hashes --quiet -o "$resolved" "${files[@]}"
uv pip sync --python .venv/bin/python "${wheels[@]}" "$resolved"
