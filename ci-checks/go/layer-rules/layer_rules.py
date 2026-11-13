"""Layer rules for Go: depguard, through golangci-lint, with the depguard rules of the project's own config.

Run from the project's working-directory. golangci-lint never sees the project's config itself: it loads every Go plugin a
config names whatever linters are on, and the config can write output files or narrow the run to new issues. So this
script copies only the settings that decide what depguard reports into a config of its own, read the way golangci-lint
reads them.
"""

import argparse
import json
import re
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
# golangci-lint's own: 1 means issues, anything else that it could not run.
ISSUES = 1
CONFIG_PATH = "${config-path}"


class YAML12Loader(yaml.SafeLoader):
    """Scalars as golangci-lint's yaml.v3 reads them: no timestamps, and only true and false are booleans."""


YAML12Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
YAML12Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))

PARSERS: dict[str, Callable[[str], Any]] = {
    ".json": json.loads,
    ".toml": tomllib.loads,
    ".yaml": lambda text: yaml.load(text, Loader=YAML12Loader),
    ".yml": lambda text: yaml.load(text, Loader=YAML12Loader),
}


class Error(Exception):
    pass


def find_config(directory: Path) -> Path | None:
    return next((directory / name for name in CONFIG_NAMES if (directory / name).is_file()), None)


def lower_keys(value: Any) -> Any:
    # viper matches keys ignoring case, so Linters.Settings.Depguard is linters.settings.depguard.
    if isinstance(value, dict):
        return {key.lower() if isinstance(key, str) else key: lower_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [lower_keys(item) for item in value]
    return value


def load(path: Path) -> dict[str, Any]:
    try:
        config = PARSERS[path.suffix](path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as error:
        raise Error(f"{path.name}: cannot read it: {error}") from None
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise Error(f"{path.name}: the top level is not a mapping")
    return lower_keys(config)


def section(config: dict[str, Any], path: Path, *keys: str) -> dict[str, Any]:
    """The mapping at keys, empty when absent; golangci-lint rejects any other value there."""
    for depth, key in enumerate(keys, 1):
        config = config.get(key, {})
        if not isinstance(config, dict):
            raise Error(f"{path.name}: {'.'.join(keys[:depth])} is not a mapping")
    return config


def dig(config: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(config, dict):
            return None
        config = config.get(key)
    return config


def version(value: Any) -> str:
    # golangci-lint decodes version weakly into a string: 2 and 2.0 both read "2".
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def depguard_config(project: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """The config golangci-lint runs with, or None when the project has no depguard rules."""
    if version(project.get("version")) != "2":
        if dig(project, "linters-settings", "depguard") or dig(project, "linters", "settings", "depguard"):
            raise Error(f'{path.name} has depguard settings but is not a golangci-lint v2 config: add version: "2" (golangci-lint migrate)')
        return None
    depguard = section(project, path, "linters", "settings", "depguard")
    if not depguard.get("rules"):
        return None
    run = section(project, path, "run")
    # The project's config is in the working directory and this one is not, so cfg becomes wd.
    mode = run.get("relative-path-mode") or "cfg"
    generated_run = {"relative-path-mode": "wd" if mode == "cfg" else mode}
    generated_run.update({key: run[key] for key in ("build-tags", "tests") if key in run})
    linters: dict[str, Any] = {
        "default": "none",
        "enable": ["depguard"],
        "settings": {"depguard": with_config_dir(depguard, path.parent.resolve())},
    }
    exclusions = section(project, path, "linters").get("exclusions")
    if exclusions is not None:
        linters["exclusions"] = exclusions
    return {
        "version": "2",
        "run": generated_run,
        "linters": linters,
        "issues": {"max-issues-per-linter": 0, "max-same-issues": 0},
    }


def with_config_dir(depguard: dict[str, Any], directory: Path) -> dict[str, Any]:
    # golangci-lint puts its config's directory for ${config-path} in each rule's files; this config is elsewhere.
    rules = depguard["rules"]
    if not isinstance(rules, dict):
        return depguard
    fixed = {}
    for name, rule in rules.items():
        files = rule.get("files") if isinstance(rule, dict) else None
        if isinstance(files, list):
            rule = {**rule, "files": [f.replace(CONFIG_PATH, str(directory)) if isinstance(f, str) else f for f in files]}
        fixed[name] = rule
    return {**depguard, "rules": fixed}


def run_tool(golangci_lint: str, config: dict[str, Any]) -> int:
    try:
        text = json.dumps(config)
    except (TypeError, ValueError) as error:
        raise Error(f"a depguard setting has a value golangci-lint cannot read: {error}") from None
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "golangci.json"
        path.write_text(text, encoding="utf-8")
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
