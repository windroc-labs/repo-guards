import datetime
import json
import math
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

PROJECT = "project"
DEFAULT_SOURCE_DIRS = ["src"]
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
USAGE = """usage: config_overrides.py apply ROOT OVERRIDES
       config_overrides.py source-dirs OVERRIDES WORKING_DIRECTORY
       config_overrides.py get FILE KEY..."""


class Error(Exception):
    pass


def merge(default: Any, override: Any) -> Any:
    if not (isinstance(default, dict) and isinstance(override, dict)):
        return override
    merged = dict(default)
    for key, value in override.items():
        merged[key] = merge(merged[key], value) if key in merged else value
    return merged


def toml_key(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else toml_string(key)


def toml_string(text: str) -> str:
    out = []
    for char in text:
        if char in ESCAPES:
            out.append(ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
    raise Error(f"cannot write {type(value).__name__} as TOML")


def toml_lines(table: dict[str, Any], path: tuple[str, ...]) -> list[str]:
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
    lines: list[str] = []
    # A table holding only sub-tables is defined by their headers; an empty one needs its own.
    if path and (scalars or not tables):
        lines.append("[" + ".".join(toml_key(p) for p in path) + "]")
    lines += [f"{toml_key(k)} = {toml_value(v)}" for k, v in scalars]
    for key, sub in tables:
        lines += toml_lines(sub, path + (key,))
    return lines


def dump_toml(table: dict[str, Any]) -> str:
    return "\n".join(toml_lines(table, ())) + "\n"


def load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return tomllib.loads(text)
    if path.suffix == ".json":
        return json.loads(text)
    raise Error("not a .toml or .json file")


def dump(path: Path, data: Any) -> str:
    if path.suffix == ".toml":
        return dump_toml(data)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def merged_text(root: Path, section: str, override: Any) -> str:
    if not isinstance(override, dict):
        raise Error("not a table")
    path = root / section
    default = load(path)
    return dump(path, merge(default, override))


def apply(root: Path, overrides: Path) -> int:
    if not overrides.exists():
        print(f"{overrides}: not found, using the defaults")
        return 0
    try:
        sections = tomllib.loads(overrides.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"config_overrides: {overrides}: {error}", file=sys.stderr)
        return 2
    # Every section is merged before any is written, so a bad one leaves all files untouched.
    writes: list[tuple[str, str]] = []
    failed = False
    for section, override in sections.items():
        if section == PROJECT:
            continue
        try:
            writes.append((section, merged_text(root, section, override)))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, Error) as error:
            print(f'config_overrides: ["{section}"]: {error}', file=sys.stderr)
            failed = True
    if failed:
        return 2
    for section, merged in writes:
        (root / section).write_text(merged, encoding="utf-8")
        print(f'{overrides}: applied ["{section}"]')
    return 0


def source_dirs(overrides: Path, working_directory: str) -> list[str]:
    if not overrides.exists():
        return DEFAULT_SOURCE_DIRS
    project = tomllib.loads(overrides.read_text(encoding="utf-8")).get(PROJECT, {})
    return project.get(working_directory, {}).get("source-dirs", DEFAULT_SOURCE_DIRS)


def text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def get(path: Path, keys: list[str]) -> list[str]:
    value = load(path)
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise Error(f"{path}: no {'.'.join(keys)}")
        value = value[key]
    return [text(item) for item in value] if isinstance(value, list) else [text(value)]


def main(argv: list[str]) -> int:
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if command == "apply" and len(args) == 2:
            return apply(Path(args[0]), Path(args[1]))
        if command == "source-dirs" and len(args) == 2:
            print(" ".join(source_dirs(Path(args[0]), args[1])))
            return 0
        if command == "get" and len(args) >= 2:
            for line in get(Path(args[0]), args[1:]):
                print(line)
            return 0
    except (OSError, UnicodeDecodeError, ValueError, Error) as error:
        print(f"config_overrides: {error}", file=sys.stderr)
        return 2
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
