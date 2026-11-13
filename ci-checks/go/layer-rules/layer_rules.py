"""Layer rules for Go: depguard, through golangci-lint, with the depguard rules of the project's own config.

Run from the project's working-directory. golangci-lint never sees the project's config itself: it loads every Go plugin a
config names whatever linters are on, and the config can write output files or narrow the run to new issues. So this
script copies only the settings that decide what depguard reports into a config of its own.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

# The order golangci-lint picks one in (viper's SupportedExts); only the working directory, never its parents.
CONFIG_NAMES = (".golangci.json", ".golangci.toml", ".golangci.yaml", ".golangci.yml")
PARSERS: dict[str, Callable[[str], Any]] = {
    ".json": json.loads,
    ".toml": tomllib.loads,
    ".yaml": yaml.safe_load,
    ".yml": yaml.safe_load,
}
# golangci-lint's own: 1 means issues, anything else that it could not run.
ISSUES = 1


class Error(Exception):
    pass


def find_config(directory: Path) -> Path | None:
    return next((directory / name for name in CONFIG_NAMES if (directory / name).is_file()), None)


def load(path: Path) -> dict[str, Any]:
    try:
        config = PARSERS[path.suffix](path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as error:
        raise Error(f"{path.name}: cannot read it: {error}") from None
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise Error(f"{path.name}: the top level is not a mapping")
    return config


def dig(config: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(config, dict):
            return None
        config = config.get(key)
    return config


def depguard_config(project: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """The config golangci-lint runs with, or None when the project has no depguard rules."""
    if str(project.get("version")) != "2":
        if dig(project, "linters-settings", "depguard") or dig(project, "linters", "settings", "depguard"):
            raise Error(f'{path.name} has depguard settings but is not a golangci-lint v2 config: add version: "2" (golangci-lint migrate)')
        return None
    depguard = dig(project, "linters", "settings", "depguard")
    if not dig(depguard, "rules"):
        return None
    run = project.get("run") if isinstance(project.get("run"), dict) else {}
    # The project's config is in the working directory and this one is not, so cfg becomes wd.
    mode = run.get("relative-path-mode") or "cfg"
    generated_run = {"relative-path-mode": "wd" if mode == "cfg" else mode}
    generated_run.update({key: run[key] for key in ("build-tags", "tests") if key in run})
    linters: dict[str, Any] = {"default": "none", "enable": ["depguard"], "settings": {"depguard": depguard}}
    exclusions = dig(project, "linters", "exclusions")
    if exclusions is not None:
        linters["exclusions"] = exclusions
    return {
        "version": "2",
        "run": generated_run,
        "linters": linters,
        "issues": {"max-issues-per-linter": 0, "max-same-issues": 0},
    }


def run_tool(golangci_lint: str, config: dict[str, Any]) -> int:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "golangci.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        try:
            return subprocess.run([golangci_lint, "run", "--config", str(path), "./..."], check=False).returncode
        except OSError as error:
            raise Error(f"cannot run {golangci_lint}: {error}") from None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golangci-lint", default="golangci-lint", help="the golangci-lint binary")
    args = parser.parse_args(argv)
    directory = Path.cwd()
    try:
        path = find_config(directory)
        if path is None:
            print(f"Layer rules: no golangci-lint config ({', '.join(CONFIG_NAMES)}) in {directory}; depguard not run.")
            return 0
        config = depguard_config(load(path), path)
        if config is None:
            print(f"Layer rules: {path.name} has no depguard rules (linters.settings.depguard.rules); depguard not run.")
            return 0
        print(f"Layer rules: depguard with the rules in {path.name}, on the whole project.", flush=True)
        code = run_tool(args.golangci_lint, config)
    except Error as error:
        print(f"Layer rules: {error}", file=sys.stderr)
        return 2
    if code == 0:
        return 0
    return 1 if code == ISSUES else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
