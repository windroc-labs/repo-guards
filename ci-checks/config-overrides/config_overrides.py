import datetime
import json
import math
import os
import re
import stat
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROJECT = "project"
DEFAULT_SOURCE_DIRS = ["src"]
OVERRIDES = ".github/checks/repo-guards.toml"
API_SNAPSHOT = "api-snapshot"
API_SNAPSHOT_SCOPE = "api-snapshot-scope"
LANGUAGES = ("rust", "python", "typescript", "go")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
GO_SEGMENT = re.compile(r"[A-Za-z0-9._~+-]+")
ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
USAGE = f"""usage: config_overrides.py apply ROOT OVERRIDES
       config_overrides.py source-dirs OVERRIDES WORKING_DIRECTORY
       config_overrides.py snapshot-path REPO_ROOT WORKING_DIRECTORY {{{'|'.join(LANGUAGES)}}}
       config_overrides.py snapshot-scope REPO_ROOT WORKING_DIRECTORY {{{'|'.join(LANGUAGES)}}}
       config_overrides.py get FILE KEY..."""

Mode = Callable[[str], int | None]


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


def section_header(working_directory: str) -> str:
    return f"[{PROJECT}.{json.dumps(working_directory, ensure_ascii=False)}]"


def project_sections(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    project = overrides.get(PROJECT, {})
    if not isinstance(project, dict):
        raise Error(f"[{PROJECT}] is not a table")
    for working_directory, section in project.items():
        if not isinstance(section, dict):
            raise Error(f"{section_header(working_directory)} is not a table")
    return project


def parse_overrides(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise Error(f"{OVERRIDES}: {error}") from error


def source_dirs(overrides: Path, working_directory: str) -> list[str]:
    if not overrides.exists():
        return DEFAULT_SOURCE_DIRS
    project = project_sections(parse_overrides(overrides.read_text(encoding="utf-8")))
    return project.get(working_directory, {}).get("source-dirs", DEFAULT_SOURCE_DIRS)


def language_settings(
    overrides: dict[str, Any], key: str, valid: Callable[[Any], bool], expected: str
) -> dict[tuple[str, str], Any]:
    """Every [project."<working-directory>"] <key>.<language> value, checked for language and type."""
    settings: dict[tuple[str, str], Any] = {}
    for working_directory, section in project_sections(overrides).items():
        values = section.get(key, {})
        where = f"{section_header(working_directory)} {key}"
        if not isinstance(values, dict):
            raise Error(f"{where} is not a table")
        for language, value in values.items():
            if language not in LANGUAGES:
                raise Error(f"{where}: unknown language {json.dumps(language)}; expected one of {', '.join(LANGUAGES)}")
            if not valid(value):
                raise Error(f"{where}.{language} is not {expected}")
            settings[working_directory, language] = value
    return settings


def snapshot_settings(overrides: dict[str, Any]) -> dict[tuple[str, str], str]:
    return language_settings(overrides, API_SNAPSHOT, lambda value: isinstance(value, str), "a string")


def scope_settings(overrides: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    def valid(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(prefix, str) for prefix in value)

    return language_settings(overrides, API_SNAPSHOT_SCOPE, valid, "an array of strings")


def read_overrides(root: Path) -> dict[str, Any]:
    path = root / OVERRIDES
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise Error(f"{OVERRIDES}: {error}") from error
    return parse_overrides(text)


def read_snapshot_settings(root: Path) -> dict[tuple[str, str], str]:
    return snapshot_settings(read_overrides(root))


def checkout_mode(root: Path) -> Mode:
    def mode(path: str) -> int | None:
        try:
            return os.lstat(root / path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            return None

    return mode


def snapshot_path_error(path: str, mode: Mode) -> str | None:
    # Paths are compared with git's output, so only one spelling of each file is accepted.
    parts = path.split("/")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return "must not contain control characters"
    if any(part in ("", ".", "..") for part in parts):
        return 'must be a relative path without empty, "." or ".." parts'
    if parts[0] != ".github" or len(parts) < 2:
        return "must be a file under .github/"
    if parts[1] == "workflows":
        return "must not be under .github/workflows/"
    if path == OVERRIDES:
        return f"must not be {OVERRIDES}"
    for end in range(1, len(parts) + 1):
        prefix = "/".join(parts[:end])
        found = mode(prefix)
        if found is not None and stat.S_ISLNK(found):
            return f"{json.dumps(prefix, ensure_ascii=False)} is a symbolic link"
    found = mode(path)
    if found is not None and not stat.S_ISREG(found):
        return "is not a regular file"
    return None


def snapshot_path(root: Path, working_directory: str, language: str) -> str | None:
    path = read_snapshot_settings(root).get((working_directory, language))
    if path is None:
        return None
    error = snapshot_path_error(path, checkout_mode(root))
    if error:
        where = f"{section_header(working_directory)} {API_SNAPSHOT}.{language}"
        raise Error(f"{where} = {json.dumps(path, ensure_ascii=False)}: {error}")
    return path


def identifiers(prefix: str, separator: str) -> bool:
    return all(part.isidentifier() for part in prefix.split(separator))


def go_package(prefix: str) -> bool:
    return all(GO_SEGMENT.fullmatch(part) and part not in (".", "..") for part in prefix.split("/"))


def typescript_subpath(prefix: str) -> bool:
    if prefix == ".":
        return True
    parts = prefix.removeprefix("./").split("/")
    return prefix.startswith("./") and all(part not in ("", ".", "..") and "*" not in part for part in parts)


# Per language: whether a prefix is spelled as the generator names modules, and an example for the message.
SCOPE_PREFIXES: dict[str, tuple[Callable[[str], bool], str]] = {
    "rust": (lambda prefix: identifiers(prefix, "::"), 'a module path such as "kvm_op::driver"'),
    "python": (lambda prefix: identifiers(prefix, "."), 'a dotted module name such as "pkg.sub"'),
    "go": (go_package, 'a package import path such as "example.com/mod/driver"'),
    "typescript": (typescript_subpath, '"." or an export subpath such as "./driver"'),
}


def scope_prefix_error(language: str, prefix: str) -> str | None:
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in prefix):
        return "must not contain control characters"
    valid, example = SCOPE_PREFIXES[language]
    return None if valid(prefix) else f"must be {example}"


def snapshot_scope(root: Path, working_directory: str, language: str) -> list[str] | None:
    prefixes = scope_settings(read_overrides(root)).get((working_directory, language))
    if prefixes is None:
        return None
    where = f"{section_header(working_directory)} {API_SNAPSHOT_SCOPE}.{language}"
    if not prefixes:
        raise Error(f"{where} = []: must list at least one module prefix; leave it unset to freeze the whole project")
    for prefix in prefixes:
        error = scope_prefix_error(language, prefix)
        if error:
            raise Error(f"{where}: {json.dumps(prefix, ensure_ascii=False)}: {error}")
    return prefixes


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
        if command == "snapshot-path" and len(args) == 3 and args[2] in LANGUAGES:
            path = snapshot_path(Path(args[0]), args[1], args[2])
            if path is not None:
                print(path)
            return 0
        if command == "snapshot-scope" and len(args) == 3 and args[2] in LANGUAGES:
            prefixes = snapshot_scope(Path(args[0]), args[1], args[2])
            if prefixes is not None:
                print(json.dumps(prefixes, ensure_ascii=False, separators=(",", ":")))
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
