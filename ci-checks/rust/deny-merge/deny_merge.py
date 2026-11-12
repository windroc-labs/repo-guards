import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ALLOWLIST_KEYS = {
    ("bans", "allow"),
    ("bans", "build", "allow-build-scripts"),
    ("sources", "allow-git"),
    ("sources", "allow-registry"),
    ("licenses", "exceptions"),
}


class Error(Exception):
    pass


def leaves(table: dict[str, Any], path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    for key, value in table.items():
        if isinstance(value, dict):
            yield from leaves(value, path + (key,))
        else:
            yield path + (key,), value


def merge(shared: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    for path, value in leaves(project):
        name = ".".join(path)
        if path not in ALLOWLIST_KEYS:
            raise Error(f"{name}: not an allowlist key; allowed: {', '.join(sorted('.'.join(k) for k in ALLOWLIST_KEYS))}")
        if not isinstance(value, list):
            raise Error(f"{name}: must be an array")
        # cargo-deny bans every crate a non-empty bans.allow leaves out; dependency_allowlist.py checks PR changes instead.
        if path == ("bans", "allow"):
            continue
        node = shared
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = node.get(path[-1], []) + value
    return shared


def value(item: Any) -> str:
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float, str)):
        return json.dumps(item)
    if isinstance(item, list):
        return "[" + ", ".join(value(x) for x in item) + "]"
    if isinstance(item, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {value(x)}" for k, x in item.items()) + " }"
    raise Error(f"unsupported value: {item!r}")


def dump(table: dict[str, Any], path: tuple[str, ...] = ()) -> list[str]:
    lines: list[str] = []
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    if path and scalars:
        lines.append("[" + ".".join(json.dumps(p) for p in path) + "]")
    lines += [f"{json.dumps(k)} = {value(v)}" for k, v in scalars]
    for key, sub in table.items():
        if isinstance(sub, dict):
            lines += dump(sub, path + (key,))
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: deny_merge.py SHARED_DENY_TOML LICENSES_TOML PROJECT_ALLOWLIST_TOML OUTPUT", file=sys.stderr)
        return 2
    shared_path, licenses_path, project_path, output = (Path(a) for a in argv)
    try:
        shared = tomllib.loads(shared_path.read_text())
        shared.setdefault("licenses", {})["allow"] = tomllib.loads(licenses_path.read_text())["allow"]
        project = tomllib.loads(project_path.read_text())
        output.write_text("\n".join(dump(merge(shared, project))) + "\n")
    except (OSError, tomllib.TOMLDecodeError, Error) as error:
        print(f"deny_merge: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
