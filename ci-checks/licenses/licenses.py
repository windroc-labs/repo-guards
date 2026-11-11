import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

RUST = Path(__file__).resolve().parent.parent / "rust"
TOKEN = re.compile(r"\(|\)|[^\s()]+")


class Error(Exception):
    pass


def allowed(deny: Path, classifiers: Path) -> set[str]:
    spdx = set(tomllib.loads(deny.read_text())["licenses"]["allow"])
    mapping = tomllib.loads(classifiers.read_text())
    wider = set(mapping) - spdx
    if wider:
        raise Error(f"{classifiers} maps licenses missing from deny.toml: {sorted(wider)}")
    return spdx | {str(name) for name in mapping.values()}


def expression_ok(text: str, allow: set[str]) -> bool:
    tokens = TOKEN.findall(text.strip().rstrip("*"))
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        result = parse_and()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            result = parse_and() or result
        return result

    def parse_and() -> bool:
        nonlocal pos
        result = parse_atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            result = parse_atom() and result
        return result

    def parse_atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            raise Error(f"bad license expression {text!r}")
        token = tokens[pos]
        pos += 1
        if token == "(":
            result = parse_or()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise Error(f"bad license expression {text!r}")
            pos += 1
            return result
        return token.rstrip("*") in allow

    if text.strip() in allow:
        return True
    try:
        result = parse_or()
    except Error:
        return False
    return result and pos == len(tokens)


def licenses(fmt: str, data: object) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if fmt == "pip":
        if not isinstance(data, list):
            raise Error("pip-licenses output is not a list")
        for entry in data:
            found[entry["Name"]] = [part.strip() for part in str(entry["License"]).split(";")]
    else:
        if not isinstance(data, dict):
            raise Error("license-checker output is not an object")
        for key, entry in data.items():
            value = entry.get("licenses", "UNKNOWN")
            found[key.rpartition("@")[0] or key] = value if isinstance(value, list) else [str(value)]
    return found


def exceptions(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Judge a license report against the shared allowlist.")
    parser.add_argument("--format", choices=("pip", "npm"), required=True)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--deny", type=Path, default=RUST / "deny.toml")
    parser.add_argument("--classifiers", type=Path, default=RUST / "license-classifiers.toml")
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        allow = allowed(args.deny, args.classifiers)
        found = licenses(args.format, json.loads(args.report.read_text()))
    except (OSError, ValueError, KeyError, AttributeError, Error) as error:
        print(f"licenses: {error}", file=sys.stderr)
        return 2
    skip = exceptions(args.exceptions)
    bad = {
        name: names
        for name, names in found.items()
        if name not in skip and not any(expression_ok(n, allow) for n in names)
    }
    for name, names in sorted(bad.items()):
        print(f"::error::{name} is licensed under {'; '.join(names)}, not in the allowlist")
    print(f"{len(found)} packages, {len(bad)} not allowed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
