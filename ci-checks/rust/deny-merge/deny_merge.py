import json
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "allowlist"))
from allowlist import Error, leaves, load


def merge(shared: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """project is a validated allowlist: every leaf is an array under an allowlist key."""
    for path, value in leaves(project):
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
        output.write_text("\n".join(dump(merge(shared, load(project_path).raw))) + "\n")
    except (OSError, tomllib.TOMLDecodeError, Error) as error:
        print(f"deny_merge: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
