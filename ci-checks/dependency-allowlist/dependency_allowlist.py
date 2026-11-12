import json
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

Package = tuple[str, str]


def cargo(text: str) -> set[Package]:
    # Crates without a source are the workspace's own members and path crates.
    return {(p["name"], p["version"]) for p in tomllib.loads(text).get("package", []) if "source" in p}


def pylock(text: str) -> set[Package]:
    return {(p["name"], p.get("version", "")) for p in tomllib.loads(text).get("packages", [])}


def npm(text: str) -> set[Package]:
    packages: dict[str, dict[str, Any]] = json.loads(text)["packages"]
    return {
        (p.get("name", key.rsplit("node_modules/", 1)[1]), p["version"])
        for key, p in packages.items()
        if "node_modules/" in key and not p.get("link")
    }


READERS: dict[str, Callable[[str], set[Package]]] = {"cargo": cargo, "pylock": pylock, "npm": npm}


def entry(item: str | dict[str, str]) -> tuple[str, str | None]:
    if isinstance(item, dict):
        if "crate" not in item:
            return item["name"], item.get("version")
        item = item["crate"]
    # rpartition keeps a scoped npm name (@scope/pkg) whole.
    name, _, version = item.rpartition("@")
    return (name, version) if name else (item, None)


def spec(package: Package) -> str:
    name, version = package
    return f"{name}@{version}" if version else name


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] not in READERS:
        print(f"usage: dependency_allowlist.py {{{'|'.join(READERS)}}} ALLOWLIST BASE_LOCK HEAD_LOCK", file=sys.stderr)
        return 2
    read = READERS[argv[0]]
    allowlist, base, head = (Path(a) for a in argv[1:])
    config = tomllib.loads(allowlist.read_text()) if allowlist.exists() else {}
    allowed = {entry(item) for item in config.get("bans", {}).get("allow", [])}
    new = read(head.read_text()) - (read(base.read_text()) if base.exists() else set())
    denied = sorted(p for p in new if (p[0], None) not in allowed and p not in allowed)
    for package in denied:
        print(f'{spec(package)}: not in bans.allow of {allowlist}; add "{spec(package)}"')
    return 1 if denied else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
