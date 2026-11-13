import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config-overrides"))
import config_overrides  # noqa: E402

OVERRIDES = config_overrides.OVERRIDES
SECTION = "ci-checks/inline-exemptions/settings.toml"
USAGE = "usage: github_dir_pr.py BASE_REF  (run from the repo root)"


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check)


def changed_files(root: Path, base_ref: str) -> list[str]:
    out = git(root, "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", "-z", f"{base_ref}...HEAD").stdout
    return [path for path in out.split("\0") if path]


def overrides_text(root: Path, rev: str) -> str:
    """The overrides file at rev; empty, like an empty file, when rev has none."""
    shown = git(root, "show", f"{rev}:{OVERRIDES}", check=False)
    return shown.stdout if shown.returncode == 0 else ""


def without_allowlist(root: Path, rev: str) -> Any:
    """The overrides at rev minus the inline-exemptions allowlist; None when unreadable."""
    try:
        data = tomllib.loads(overrides_text(root, rev))
    except tomllib.TOMLDecodeError:
        return None
    section = data.get(SECTION)
    if isinstance(section, dict):
        section.pop("exemption", None)
        if not section:
            del data[SECTION]
    return data


def allowlist_only(root: Path, merge_base: str, github: list[str]) -> bool:
    if github != [OVERRIDES]:
        return False
    head = without_allowlist(root, "HEAD")
    return head is not None and head == without_allowlist(root, merge_base)


def tree_modes(root: Path, rev: str) -> dict[str, int]:
    """The git mode of each entry under .github/ at rev, directories included."""
    out = git(root, "ls-tree", "-r", "-t", "-z", rev, "--", ".github").stdout
    modes = {}
    for entry in filter(None, out.split("\0")):
        meta, path = entry.split("\t", 1)
        modes[path] = int(meta.split(" ", 1)[0], 8)
    return modes


def snapshot_files(root: Path, merge_base: str) -> set[str]:
    """The API snapshot paths the merge-base's overrides set, minus those the API snapshot job
    would reject at either end of the PR (so a PR cannot turn a snapshot into a symlink)."""
    try:
        overrides = config_overrides.parse_overrides(overrides_text(root, merge_base))
        settings = config_overrides.snapshot_settings(overrides)
    except config_overrides.Error:
        return set()
    trees = [tree_modes(root, merge_base), tree_modes(root, "HEAD")]
    error = config_overrides.snapshot_path_error
    return {
        path
        for (_, language), path in settings.items()
        if all(error(path, language, tree.get) is None for tree in trees)
    }


def check(root: Path, base_ref: str) -> int:
    files = changed_files(root, base_ref)
    github = [f for f in files if f.startswith(".github/")]
    others = [f for f in files if not f.startswith(".github/")]
    if not (github and others):
        return 0
    merge_base = git(root, "merge-base", base_ref, "HEAD").stdout.strip()
    snapshots = snapshot_files(root, merge_base)
    github = [f for f in github if f not in snapshots]
    if not github or allowlist_only(root, merge_base, github):
        return 0
    print("::error::A PR that changes .github/ must change nothing else")
    print(".github files:")
    print("".join(f"  {json.dumps(f, ensure_ascii=False)}\n" for f in github), end="")
    print("Other files:")
    print("".join(f"  {json.dumps(f, ensure_ascii=False)}\n" for f in others), end="")
    print(f'Only the "exemption" list of ["{SECTION}"] in {OVERRIDES} may change beside other files,')
    print(f'and the snapshot files [project."<working-directory>"] api-snapshot in {OVERRIDES} sets at the merge-base.')
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
