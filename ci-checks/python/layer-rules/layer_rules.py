"""Runs import-linter on a project's own config without executing any of the project's code.

Usage: python -I layer_rules.py PROJECT [SOURCE_DIR ...], with the venv that has import-linter installed.
SOURCE_DIRS are relative to PROJECT; with PROJECT itself they are where the root packages are looked for.
"""

import os
import secrets
import sys
import tomllib
from collections.abc import Sequence
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType

PREFIX = "layer rules (python): "
INI = ".importlinter"
PYPROJECT = "pyproject.toml"


class Error(Exception):
    pass


def find_config(project: Path) -> Path | None:
    # import-linter's own lookup reads INI files before pyproject.toml, so .importlinter wins.
    ini = project / INI
    if ini.is_file():
        return ini
    pyproject = project / PYPROJECT
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Error(f"cannot read {PYPROJECT}: {error}") from error
    tool = data.get("tool")
    return pyproject if isinstance(tool, dict) and "importlinter" in tool else None


class ProjectFinder:
    """Locates the project's packages for grimp, which only reads their files, and keeps them from running.

    Its specs have no loader: importing a project package gives an empty namespace and importing a project module
    fails, so neither the graph build nor a contract_types entry naming project code executes it.
    """

    def __init__(self, roots: Sequence[Path], top_names: frozenset[str]):
        self.roots = [str(root) for root in roots]
        self.top_names = top_names

    def inside(self, entry: str) -> bool:
        return any(Path(entry).resolve().is_relative_to(root) for root in self.roots)

    def find_spec(
        self, name: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if path is None:
            if name not in self.top_names:
                return None
            path = self.roots
        elif not any(self.inside(entry) for entry in path):
            return None
        spec = PathFinder.find_spec(name, path)
        if spec is not None:
            spec.loader = None
        return spec


def lint(config: Path, roots: Sequence[Path]) -> bool:
    from importlinter import configuration
    from importlinter.application import use_cases

    configuration.configure()
    try:
        options = use_cases.read_user_options(config_filename=str(config))
    except Exception:  # noqa: BLE001 -- lint_imports reads the config again and reports the error itself.
        top_names: frozenset[str] = frozenset()
    else:
        top_names = frozenset(name.split(".")[0] for name in options.session_options.get("root_packages", []))
    sys.meta_path.insert(0, ProjectFinder(roots, top_names))
    # use_cases rather than the CLI, which puts the cwd first on sys.path, where project files would shadow modules.
    return use_cases.lint_imports(config_filename=str(config), cache_dir=None, no_logo=True)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: layer_rules.py PROJECT [SOURCE_DIR ...]", file=sys.stderr)
        return 2
    project = Path(argv[0]).resolve()
    try:
        config = find_config(project)
    except Error as error:
        print(f"{PREFIX}{error}", file=sys.stderr)
        return 2
    if config is None:
        print(f"{PREFIX}no import-linter config in {INI} or {PYPROJECT} [tool.importlinter]; skipped")
        return 0
    roots = [project, *(root for root in (project / name for name in argv[1:]) if root.is_dir())]
    searched = ", ".join(str(root.relative_to(project)) for root in roots)
    print(f"{PREFIX}config {config.name}, packages looked for in: {searched}", flush=True)
    # The report quotes the PR's file names and source lines; no line of it may act as a workflow command.
    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}", flush=True)
    sys.stderr.flush()
    os.dup2(sys.stdout.fileno(), sys.stderr.fileno())
    try:
        passed = lint(config, roots)
    except ImportError as error:
        print(f"{PREFIX}cannot run import-linter: {error}")
        return 2
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        print(f"::{token}::", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
