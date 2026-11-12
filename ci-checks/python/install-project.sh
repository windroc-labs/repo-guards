#!/usr/bin/env bash
# Usage: install-project.sh resolve|sync RESOLVED, run in the project's directory.
# A uv project (uv.lock) needs no resolve; sync installs it as locked. Otherwise resolve creates .venv and resolves the
# files in REQUIREMENTS (space-separated) and the present REQUIREMENTS_DEV files into RESOLVED, with each package's
# index, and sync installs RESOLVED into .venv. Dependency rules checks the lock or RESOLVED between the two.
# Only the packages in UV_NO_BINARY_PACKAGE build from source: uv sync reads it with UV_NO_BUILD, uv pip reads neither.
# Before resolving, each git requirement the allowlist (ALLOWLIST) admits is built alone, so the resolve, which builds
# nothing, finds its metadata in uv's cache even when the line names no package.
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
    hint="::error::Resolving builds only what the allowlist admits. If uv says building source distributions is disabled: for a git or URL line in the project's requirement files, add its repository URL to python.sources.allow-git or its URL prefix to python.sources.allow-registry; for a PyPI sdist, or a git or URL source another package depends on, add its package name to python.bans.build.allow-build-scripts."
    allowlist=(uv run --no-project --python 3.12 "$(dirname "$0")/../allowlist/allowlist.py")
    "${allowlist[@]}" prebuild python --allowlist "$ALLOWLIST" "${files[@]}" > "$resolved.prebuild"
    while IFS= read -r requirement; do
      if ! uv pip compile --python .venv/bin/python --no-deps --quiet -o /dev/null - <<< "$requirement"; then
        echo "::error::Building the admitted requirement $requirement failed."
        exit 1
      fi
    done < "$resolved.prebuild"
    if ! uv pip compile --python .venv/bin/python "${wheels[@]}" --emit-index-annotation --generate-hashes --quiet \
      -o "$resolved" "${files[@]}"; then
      echo "$hint"
      exit 1
    fi
    ;;
  sync)
    uv pip sync --python .venv/bin/python "${wheels[@]}" "$resolved"
    ;;
  *)
    echo "usage: install-project.sh resolve|sync RESOLVED" >&2
    exit 2
    ;;
esac
