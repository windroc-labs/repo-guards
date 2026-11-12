import re
import tomllib
from pathlib import PurePosixPath
from typing import Any

from syntax import SourceError

Hit = tuple[str, str, str]

GO_COMMENT = re.compile(r"//.*")
IGNORE_LINE = re.compile(r"ignore\s+(?!\()(.+)")
IGNORE_BLOCK = re.compile(r"ignore\s*\(")


def table(data: Any, *keys: str) -> dict[str, Any]:
    for key in keys:
        data = data.get(key, {}) if isinstance(data, dict) else {}
    return data if isinstance(data, dict) else {}


def strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def lint_rule(tool: str, name: str) -> Hit:
    if tool == "clippy":
        return ("clippy", f"clippy::{name}", "")
    return ("rustc", name if tool == "rust" else f"{tool}::{name}", "")


def cargo(data: dict[str, Any]) -> list[Hit]:
    hits = []
    for lints in (table(data, "lints"), table(data, "workspace", "lints")):
        for tool, rules in lints.items():
            for name, level in rules.items() if isinstance(rules, dict) else ():
                if (level.get("level") if isinstance(level, dict) else level) == "allow":
                    hits.append(lint_rule(tool, name))
    for owner in ("package", "workspace"):
        crates = strings(table(data, owner, "metadata", "cargo-machete").get("ignored"))
        hits += [("cargo-machete", crate, "") for crate in crates]
    return hits


def pyproject(data: dict[str, Any]) -> list[Hit]:
    deptry = table(data, "tool", "deptry")
    hits = [("deptry", code, "") for code in strings(deptry.get("ignore"))]
    for code, packages in table(deptry, "per_rule_ignores").items():
        hits += [("deptry", f"{code}:{package}", "") for package in strings(packages)]
    return hits


def go_mod(path: str, text: str, tracked: list[str]) -> list[Hit]:
    hits = []
    block = False
    for raw in text.splitlines():
        line = GO_COMMENT.sub("", raw).strip()
        if block:
            if line == ")":
                block = False
            elif line:
                hits.append(("go-mod", line.strip('"`'), ""))
        elif IGNORE_BLOCK.fullmatch(line):
            block = True
        elif match := IGNORE_LINE.fullmatch(line):
            hits.append(("go-mod", match.group(1).strip().strip('"`'), ""))
    here = PurePosixPath(path).parent
    if any(PurePosixPath(other).name == "go.mod" and PurePosixPath(other).parent in here.parents for other in tracked):
        hits.append(("go-mod", "module", ""))
    return hits


def extract(path: str, source: bytes, tracked: list[str]) -> list[Hit]:
    text = source.decode("utf-8")
    name = PurePosixPath(path).name
    if name == "go.mod":
        return go_mod(path, text, tracked)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SourceError(f"invalid TOML: {error}") from error
    return cargo(data) if name == "Cargo.toml" else pyproject(data)
