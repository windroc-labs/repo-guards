import argparse
import importlib
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from types import ModuleType

from allowlist import SECTION, AllowlistError, Key, compare, load, render, toml_string
from syntax import SourceError

SUFFIXES = {
    ".rs": "rust_source",
    ".py": "python_source",
    ".pyi": "python_source",
    **dict.fromkeys([".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"], "typescript_source"),
    ".go": "go_source",
}
CONFIG_FILES = {"Cargo.toml", "pyproject.toml", "go.mod"}
SUBMODULE, SYMLINK = "160000", "120000"
MODULES: dict[str, ModuleType] = {}


def extractor_for(path: str) -> str | None:
    pure = PurePosixPath(path)
    return "config_files" if pure.name in CONFIG_FILES else SUFFIXES.get(pure.suffix)


def module(name: str) -> ModuleType:
    if name not in MODULES:
        MODULES[name] = importlib.import_module(name)
    return MODULES[name]


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def tracked(repo: Path) -> list[tuple[str, str]]:
    # The index is HEAD's tree in a clean checkout; its modes show submodules and symlinks.
    entries = []
    for record in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if record:
            info, path = record.split(b"\t", 1)
            entries.append((info.split()[0].decode(), path.decode("utf-8", "surrogateescape")))
    return entries


def scan(repo: Path) -> tuple[Counter[Key], list[tuple[str, str]]]:
    entries = [(mode, path) for mode, path in tracked(repo) if mode != SUBMODULE and extractor_for(path)]
    paths = [path for _, path in entries]
    found: Counter[Key] = Counter()
    errors = []
    for mode, path in entries:
        name = extractor_for(path)
        assert name
        file = repo / path
        try:
            if mode == SYMLINK:
                raise SourceError("is a symbolic link")
            if not file.exists():
                # Deleted in the work tree but not committed: the note above already says the run is not HEAD's.
                continue
            source = file.read_bytes()
            try:
                source.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SourceError("not UTF-8") from error
            extract = module(name).extract
            hits = extract(path, source, paths) if name == "config_files" else extract(path, source)
        except SourceError as error:
            errors.append((path, str(error)))
            continue
        found.update((tool, path, rule, function) for tool, rule, function in hits)
    return found, errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check inline exemptions against the allowlist (check 13).")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--settings", type=Path, default=Path(__file__).parent / "settings.toml")
    args = parser.parse_args(argv)
    if git(args.repo, "status", "--porcelain", "--untracked-files=no").strip():
        print("note: the work tree has uncommitted changes to tracked files; checking the work tree")
    try:
        allowlist = load(args.settings)
    except AllowlistError as error:
        print(f"error: allowlist: {error}")
        return 2
    found, errors = scan(args.repo)
    for path, message in errors:
        print(f"error: {toml_string(path)}: {message}")
    if errors:
        return 2
    changes = compare(found, allowlist)
    if not changes:
        print(f"inline exemptions: {found.total()} found, all registered")
        return 0
    print(
        f"inline exemptions: {len(changes)} entries differ from the allowlist in [{SECTION}]"
        " of .github/checks/repo-guards.toml\n"
    )
    print(render(changes), end="")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
