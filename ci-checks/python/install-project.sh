#!/usr/bin/env bash
# Usage: install-project.sh resolve|sync RESOLVED, run in the project's directory.
# A uv project (uv.lock) needs no resolve; sync installs it as locked. Otherwise resolve creates .venv and resolves the
# files in REQUIREMENTS (space-separated) and the present REQUIREMENTS_DEV files into RESOLVED, with each package's
# index, and sync installs RESOLVED into .venv. Dependency rules checks the lock or RESOLVED between the two.
# Only the packages in UV_NO_BINARY_PACKAGE build from source: uv sync reads it with UV_NO_BUILD, uv pip reads neither.
set -euo pipefail
command=$1
resolved=$2

if [[ -f uv.lock ]]; then
  if [[ $command == sync ]]; then
    uv sync --locked
  fi
  exit 0
fi

wheels=(--only-binary :all:)
read -ra built <<< "${UV_NO_BINARY_PACKAGE:-}"
for name in "${built[@]}"; do
  wheels+=(--no-binary "$name")
done

case $command in
  resolve)
    read -ra files <<< "${REQUIREMENTS:-requirements.txt}"
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
    uv pip compile --python .venv/bin/python "${wheels[@]}" --emit-index-annotation --generate-hashes --quiet \
      -o "$resolved" "${files[@]}"
    ;;
  sync)
    uv pip sync --python .venv/bin/python "${wheels[@]}" "$resolved"
    ;;
  *)
    echo "usage: install-project.sh resolve|sync RESOLVED" >&2
    exit 2
    ;;
esac
