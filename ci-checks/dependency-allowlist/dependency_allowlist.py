import json
import re
import sys
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

Package = tuple[str, str]


def cargo(text: str) -> set[Package]:
    # Crates without a source are the workspace's own members and path crates.
    return {(p["name"], p["version"]) for p in tomllib.loads(text).get("package", []) if "source" in p}


def python(text: str) -> set[Package]:
    # pylock.toml lists packages; uv.lock lists package, where an editable or virtual source is the workspace itself.
    lock = tomllib.loads(text)
    entries = lock.get("packages") or [
        p for p in lock.get("package", []) if not p["source"].keys() & {"editable", "virtual"}
    ]
    return {(p["name"], p.get("version", "")) for p in entries}


def npm(text: str) -> set[Package]:
    packages: dict[str, dict[str, Any]] = json.loads(text)["packages"]
    return {
        (p.get("name", key.rsplit("node_modules/", 1)[1]), p["version"])
        for key, p in packages.items()
        if "node_modules/" in key and not p.get("link")
    }


NON_SPACE = re.compile(r"\S")


# go list -m -json prints indented objects one after another, not one per line.
# diff_filter.py has its own copy: both scripts run standalone, from their own directories.
def json_stream(text: str) -> Iterator[Any]:
    decoder, at = json.JSONDecoder(), 0
    while start := NON_SPACE.search(text, at):
        value, at = decoder.raw_decode(text, start.start())
        yield value


def go(text: str) -> set[Package]:
    # The build list of go list -m -json all; a Replace without a Version is a local directory, the project's own.
    modules: set[Package] = set()
    for module in json_stream(text):
        target = module.get("Replace", module)
        if not module.get("Main") and ("Replace" not in module or "Version" in target):
            modules.add((target["Path"], target["Version"]))
    return modules


READERS: dict[str, Callable[[str], set[Package]]] = {"cargo": cargo, "python": python, "npm": npm, "go": go}


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
