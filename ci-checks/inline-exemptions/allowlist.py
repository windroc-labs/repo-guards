import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

TOOLS = (
    "rustc", "clippy", "rustfmt", "cargo-llvm-cov", "cargo-test", "sanitizer", "cargo-machete",
    "ruff", "ruff-format", "pyright", "coverage", "pytest", "unittest", "vulture", "deptry",
    "tsc", "eslint", "prettier", "vitest-coverage", "knip", "vitest",
    "golangci-lint", "go-test", "go-build", "go-mod", "jscpd", "generated",
)
FIELDS = ("tool", "file", "rule", "function", "count", "reason")
SECTION = '"ci-checks/inline-exemptions/settings.toml"'

Key = tuple[str, str, str, str]
Registered = tuple[int, str]


class AllowlistError(Exception):
    pass


@dataclass(frozen=True)
class Allowlist:
    skip: frozenset[str]
    entries: dict[Key, Registered]


@dataclass(frozen=True)
class Change:
    kind: Literal["add", "count", "remove"]
    key: Key
    found: int
    registered: Registered | None


def toml_string(text: str) -> str:
    escaped = (
        f"\\u{ord(c):04X}" if ord(c) < 0x20 or ord(c) == 0x7F else "\\" + c if c in '"\\' else c for c in text
    )
    return '"' + "".join(escaped) + '"'


def describe(key: Key) -> str:
    return ", ".join(f"{field} = {toml_string(value)}" for field, value in zip(FIELDS, key))


def entry(index: int, item: Any) -> tuple[Key, Registered]:
    where = f"exemption {index}"
    if not isinstance(item, dict):
        raise AllowlistError(f"{where}: not a table")
    for field in sorted(item.keys() - set(FIELDS)):
        raise AllowlistError(f"{where}: unknown field {field}")
    for field in FIELDS:
        if field not in item:
            raise AllowlistError(f"{where}: missing field {field}")
        expected = int if field == "count" else str
        if type(item[field]) is not expected:
            raise AllowlistError(f"{where}: {field} must be a {expected.__name__}")
    if item["tool"] not in TOOLS:
        raise AllowlistError(f"{where}: tool {toml_string(item['tool'])} is not a builtin tool")
    if item["count"] < 1:
        raise AllowlistError(f"{where}: count must be a positive integer")
    if not item["reason"].strip():
        raise AllowlistError(f"{where}: reason is empty")
    key = (item["tool"], item["file"], item["rule"], item["function"])
    return key, (item["count"], item["reason"])


def load(path: Path) -> Allowlist:
    try:
        settings = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise AllowlistError(f"cannot read {path}: {error}") from error
    for key in sorted(settings.keys() - {"skip", "exemption"}):
        raise AllowlistError(f"unknown setting {toml_string(key)}")
    skip, items = settings.get("skip", []), settings.get("exemption", [])
    if not isinstance(skip, list) or not all(isinstance(tool, str) for tool in skip):
        raise AllowlistError("skip must be a list of tool names")
    for tool in skip:
        if tool not in TOOLS:
            raise AllowlistError(f"skip: {toml_string(tool)} is not a builtin tool")
    if not isinstance(items, list):
        raise AllowlistError("exemption must be a list of tables")
    entries: dict[Key, Registered] = {}
    first: dict[Key, int] = {}
    for index, item in enumerate(items, 1):
        key, registered = entry(index, item)
        if key in entries:
            raise AllowlistError(f"exemption {index}: same key as exemption {first[key]} ({describe(key)})")
        entries[key], first[key] = registered, index
    return Allowlist(frozenset(skip), entries)


def compare(found: Counter[Key], allowlist: Allowlist) -> list[Change]:
    found = Counter({key: n for key, n in found.items() if key[0] not in allowlist.skip})
    changes = []
    for key in found.keys() | allowlist.entries.keys():
        registered = allowlist.entries.get(key)
        if registered is None:
            changes.append(Change("add", key, found[key], None))
        elif key not in found:
            changes.append(Change("remove", key, 0, registered))
        elif found[key] != registered[0]:
            changes.append(Change("count", key, found[key], registered))
    # Sorted by (file, function, tool, rule).
    return sorted(changes, key=lambda c: (c.key[1], c.key[3], c.key[0], c.key[2]))


def render(changes: list[Change]) -> str:
    blocks = []
    for change in changes:
        if change.kind == "add":
            note, count, reason = "# add: fill in reason", change.found, ""
        elif change.kind == "count":
            assert change.registered
            note, (old, reason) = f"# set count to {change.found} (was {change.registered[0]})", change.registered
            count = change.found
        else:
            assert change.registered
            note, (count, reason) = "# remove: no longer in the code", change.registered
        values = [*change.key, count, reason]
        lines = [note, f"[[{SECTION}.exemption]]"]
        lines += [f"{field} = {v if isinstance(v, int) else toml_string(v)}" for field, v in zip(FIELDS, values)]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""
