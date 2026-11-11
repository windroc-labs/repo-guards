import argparse
import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FORBIDDEN_CONFIG_KEYS = {
    "rustflags",
    "rustdocflags",
    "rustc",
    "rustc-wrapper",
    "rustc-workspace-wrapper",
    "rustdoc",
    "alias",
}


def walk(value: Any, path: str) -> Iterator[tuple[str, str | None, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            yield child, key, item
            yield from walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            yield child, None, item
            yield from walk(item, child)


def config_problems(path: Path) -> list[str]:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return [f"{path}: cannot parse: {error}"]
    problems = []
    for where, key, value in walk(config, ""):
        if key in FORBIDDEN_CONFIG_KEYS:
            problems.append(f"{path}: {where}: key {key!r} is not allowed")
        if isinstance(value, str) and "cap-lints" in value:
            problems.append(f"{path}: {where}: value mentions cap-lints")
    return problems


def cargo_problems(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}: cannot read: {error}"]
    problems = []
    finished = []
    for number, line in enumerate(lines, 1):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{path}:{number}: not JSON: {line[:200]}")
            continue
        if not isinstance(message, dict):
            problems.append(f"{path}:{number}: not a JSON object")
            continue
        reason = message.get("reason")
        if reason == "build-finished":
            finished.append(message.get("success") is True)
        elif reason == "compiler-message":
            diagnostic = message.get("message")
            level = diagnostic.get("level") if isinstance(diagnostic, dict) else None
            if not isinstance(level, str) or level.startswith("error") or level == "warning":
                rendered = diagnostic.get("rendered") if isinstance(diagnostic, dict) else None
                problems.append(f"{path}:{number}: {level}\n{rendered or line}")
    if not finished:
        problems.append(f"{path}: no build-finished message")
    elif not all(finished):
        problems.append(f"{path}: build-finished reports failure")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Judge Rust check results.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("cargo", help="cargo --message-format=json output").add_argument("files", nargs="+", type=Path)
    sub.add_parser("cargo-config", help=".cargo/config files").add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    check = cargo_problems if args.command == "cargo" else config_problems
    problems = [problem for file in args.files for problem in check(file)]
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
