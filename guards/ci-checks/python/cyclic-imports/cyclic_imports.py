"""Runs pylint's cyclic-import check on a project's tracked .py files without executing any of its code.

Usage: python -I cyclic_imports.py PROJECT [SOURCE_DIR ...], with the venv that has pylint installed.
Every tracked .py file under PROJECT is checked. SOURCE_DIRs, relative to PROJECT, set where module names start for
the files in them; ones that do not exist are skipped. Other files are named the way pylint does by default.
"""

import os
import subprocess
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stop_commands import run_quoted

PREFIX = "cyclic imports (python): "
USAGE = "usage: cyclic_imports.py PROJECT [SOURCE_DIR ...]"
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
    """Keeps every module under the checkout from running.

    pylint puts the source roots first on sys.path, so a project file named like a module pylint imports later
    (json.py) would run in its place. This finder sits before PathFinder: it finds each name on sys.path without the
    checkout's directories first, and any spec it returns from the checkout has no loader, so importing it fails
    instead of running it. A path counts as the checkout's when it lies under a root as written or once symlinks are
    resolved, so a tracked symlink to elsewhere is the checkout's too. pylint reads the files itself, unaffected.
    """

    def __init__(self, roots: Sequence[Path]):
        self.roots = {Path(os.path.abspath(root)) for root in roots} | {Path(os.path.realpath(root)) for root in roots}

    def inside(self, location: str) -> bool:
        paths = (Path(os.path.abspath(location or ".")), Path(os.path.realpath(location or ".")))
        return any(path.is_relative_to(root) for path in paths for root in self.roots)

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


def git(project: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise Error("git not found") from error
    if result.returncode != 0:
        raise Error(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def tracked_python_files(project: Path) -> list[str]:
    files = (name for name in git(project, "ls-files", "-z").split("\0") if name.endswith(".py"))
    return sorted(name for name in files if (project / name).is_file())


def lint(project: Path, files: Sequence[str], roots: Sequence[Path]) -> int:
    reporter = CollectingReporter()
    args = [f"--rcfile={RCFILE}", *(str(project / name) for name in files)]
    if roots:
        args.insert(1, "--source-roots=" + ",".join(str(root) for root in roots))
    try:
        Run(args, reporter=reporter, exit=False)
    except SystemExit as error:
        print(f"pylint exited with {error.code}")
        return 2
    cycles = [message for message in reporter.messages if message.symbol == "cyclic-import"]
    # pylint's own notes on the project's pragmas (useless-option-value and the like) are not about cycles.
    unread = [
        message for message in reporter.messages if message.symbol == "syntax-error" or message.category == "fatal"
    ]
    for message in unread:
        print(f"{os.path.relpath(message.abspath, project)}:{message.line}: {message.symbol}: {message.msg}")
    for message in cycles:
        print(message.msg)
    if unread:
        count = len({message.abspath for message in unread})
        print(f"{count} files could not be read, so their imports were not checked")
        return 2
    return 1 if cycles else 0


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    project = Path(argv[0]).resolve()
    try:
        files = tracked_python_files(project)
        checkout = Path(git(project, "rev-parse", "--show-toplevel").strip())
    except Error as error:
        print(f"{PREFIX}{error}", file=sys.stderr)
        return 2
    if not files:
        print(f"{PREFIX}no tracked .py files; nothing to check")
        return 0
    source_dirs = [name for name in argv[1:] if (project / name).is_dir()]
    # pylint names a module from the first source root that holds it, so the deepest comes first.
    roots = sorted({import_root((project / name).resolve()) for name in source_dirs}, key=lambda r: -len(r.parts))
    named_from = ", ".join(source_dirs) or "pylint's default"
    print(f"{PREFIX}{len(files)} tracked .py files; module names from: {named_from}", flush=True)
    sys.meta_path.insert(sys.meta_path.index(PathFinder), ProjectGuard([checkout, project, *roots]))
    return run_quoted(lambda: lint(project, files, roots))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
