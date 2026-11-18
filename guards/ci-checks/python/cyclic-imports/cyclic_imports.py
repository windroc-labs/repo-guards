"""Runs pylint's cyclic-import check on a project's tracked source files without executing any of its code.

Usage: python -I cyclic_imports.py PROJECT SOURCE_DIR [SOURCE_DIR ...], with the venv that has pylint installed.
SOURCE_DIRs are relative to PROJECT; ones that do not exist are skipped.
"""

import os
import secrets
import subprocess
import sys
import traceback
from collections.abc import Sequence
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType

PREFIX = "cyclic imports (python): "
USAGE = "usage: cyclic_imports.py PROJECT SOURCE_DIR [SOURCE_DIR ...]"
# Only cyclic-import and syntax-error are enabled; the project's own pylint config is never read.
RCFILE = Path(__file__).resolve().with_name("pylintrc")

try:
    from pylint.lint import Run
    from pylint.reporters import CollectingReporter
except ImportError as error:
    # A broken tool install means the check could not run: exit 2, never the cycle code 1.
    print(f"{PREFIX}cannot import pylint: {error}", file=sys.stderr)
    sys.exit(2)


class Error(Exception):
    pass


class ProjectGuard:
    """Keeps every module under the project's directories from running.

    pylint puts the source roots first on sys.path, so a project file named like a module pylint imports later
    (json.py) would run in its place. This finder sits before PathFinder: it finds each name on sys.path without the
    project's directories first, and any spec it returns from the project's directories has no loader, so importing
    it fails instead of running it. pylint reads the project's files itself, so its analysis is unchanged.
    """

    def __init__(self, roots: Sequence[Path]):
        self.roots = [Path(os.path.realpath(root)) for root in roots]

    def inside(self, location: str) -> bool:
        path = Path(os.path.realpath(location or "."))
        return any(path.is_relative_to(root) for root in self.roots)

    def find_spec(
        self, name: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        entries = sys.path if path is None else path
        spec = PathFinder.find_spec(name, [entry for entry in entries if not self.inside(entry)])
        if spec is None:
            spec = PathFinder.find_spec(name, path)
        if spec is not None and any(self.inside(location) for location in locations(spec)):
            spec.loader = None
        return spec


def locations(spec: ModuleSpec) -> list[str]:
    found = list(spec.submodule_search_locations or [])
    if spec.has_location and spec.origin:
        found.append(spec.origin)
    return found


def import_root(directory: Path) -> Path:
    """The directory imports are rooted at: above every package (with __init__.py) that holds `directory`."""
    while (directory / "__init__.py").is_file() and directory.parent != directory:
        directory = directory.parent
    return directory


def tracked_python_files(project: Path, source_dirs: Sequence[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "ls-files", "-z", "--", *source_dirs],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise Error("git not found") from error
    if result.returncode != 0:
        raise Error(f"git ls-files failed: {result.stderr.strip()}")
    files = (name for name in result.stdout.split("\0") if name.endswith(".py"))
    return sorted(name for name in files if (project / name).is_file())


def lint(project: Path, files: Sequence[str], roots: Sequence[Path]) -> int:
    reporter = CollectingReporter()
    args = [
        f"--rcfile={RCFILE}",
        "--source-roots=" + ",".join(str(root) for root in roots),
        *(str(project / name) for name in files),
    ]
    try:
        Run(args, reporter=reporter, exit=False)
    except SystemExit as error:
        print(f"pylint exited with {error.code}")
        return 2
    cycles = [message for message in reporter.messages if message.symbol == "cyclic-import"]
    unread = [message for message in reporter.messages if message.symbol != "cyclic-import"]
    for message in unread:
        where = os.path.relpath(message.abspath, project)
        print(f"{where}:{message.line}: {message.symbol}: {message.msg}")
    for message in cycles:
        print(message.msg)
    if unread:
        print(f"{len(unread)} files could not be read, so their imports were not checked")
        return 2
    return 1 if cycles else 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    project = Path(argv[0]).resolve()
    source_dirs = [name for name in argv[1:] if (project / name).is_dir()]
    if not source_dirs:
        print(f"{PREFIX}no source dir found: {', '.join(argv[1:])}", file=sys.stderr)
        return 2
    try:
        files = tracked_python_files(project, source_dirs)
    except Error as error:
        print(f"{PREFIX}{error}", file=sys.stderr)
        return 2
    searched = ", ".join(source_dirs)
    if not files:
        print(f"{PREFIX}no tracked .py files in {searched}; nothing to check")
        return 0
    print(f"{PREFIX}{len(files)} files in {searched}", flush=True)
    roots = sorted({import_root((project / name).resolve()) for name in source_dirs})
    sys.meta_path.insert(sys.meta_path.index(PathFinder), ProjectGuard([project, *roots]))
    # The report quotes the PR's file names and source lines; no line of it may act as a workflow command.
    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}", flush=True)
    sys.stderr.flush()
    os.dup2(sys.stdout.fileno(), sys.stderr.fileno())
    try:
        return lint(project, files, roots)
    except Exception:  # noqa: BLE001 -- the traceback can quote the PR's text, so it must print inside the block.
        traceback.print_exc()
        return 2
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        print(f"::{token}::", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
