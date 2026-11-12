import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

OVERRIDES = ".github/checks/repo-guards.toml"
SECTION = "ci-checks/inline-exemptions/settings.toml"
USAGE = "usage: github_dir_pr.py BASE_REF  (run from the repo root)"


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check)


def changed_files(root: Path, base_ref: str) -> list[str]:
    out = git(root, "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", "-z", f"{base_ref}...HEAD").stdout
    return [path for path in out.split("\0") if path]


def without_allowlist(root: Path, rev: str) -> Any:
    """The overrides at rev minus the inline-exemptions allowlist; None when unreadable."""
    shown = git(root, "show", f"{rev}:{OVERRIDES}", check=False)
    if shown.returncode != 0:
        return {}
    try:
        data = tomllib.loads(shown.stdout)
    except tomllib.TOMLDecodeError:
        return None
    section = data.get(SECTION)
    if isinstance(section, dict):
        section.pop("exemption", None)
        if not section:
            del data[SECTION]
    return data


def allowlist_only(root: Path, base_ref: str, github: list[str]) -> bool:
    if github != [OVERRIDES]:
        return False
    merge_base = git(root, "merge-base", base_ref, "HEAD").stdout.strip()
    head = without_allowlist(root, "HEAD")
    return head is not None and head == without_allowlist(root, merge_base)


def check(root: Path, base_ref: str) -> int:
    files = changed_files(root, base_ref)
    github = [f for f in files if f.startswith(".github/")]
    others = [f for f in files if not f.startswith(".github/")]
    if not (github and others) or allowlist_only(root, base_ref, github):
        return 0
    print("::error::A PR that changes .github/ must change nothing else")
    print(".github files:")
    print("".join(f"  {f}\n" for f in github), end="")
    print("Other files:")
    print("".join(f"  {f}\n" for f in others), end="")
    print(f'Only the "exemption" list of ["{SECTION}"] in {OVERRIDES} may change beside other files.')
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return check(Path.cwd(), argv[0])
    except subprocess.CalledProcessError as error:
        print(f"github_dir_pr: git failed: {error.stderr.strip()}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
