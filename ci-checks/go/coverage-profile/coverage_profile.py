import re
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path


def merge(profiles: Iterable[list[str]], ignore: str) -> tuple[str, dict[str, int]]:
    """One block per source range: set mode keeps covered by any, count modes add the hits."""
    pattern = re.compile(ignore)
    mode = ""
    blocks: dict[str, int] = {}
    for lines in profiles:
        if not lines or not lines[0].startswith("mode: "):
            raise ValueError("a profile does not start with a mode line")
        if mode and lines[0] != mode:
            raise ValueError(f"profiles of different modes: {mode!r}, {lines[0]!r}")
        mode = lines[0]
        for line in lines[1:]:
            if not line.strip():
                continue
            block, _, count = line.strip().rpartition(" ")
            hits = int(count)
            if not block or pattern.search(block.rpartition(":")[0]):
                continue
            old = blocks.get(block, 0)
            blocks[block] = max(old, hits) if mode == "mode: set" else old + hits
    return mode, blocks


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: coverage_profile.py COVERAGE_TOML OUT PROFILE...", file=sys.stderr)
        return 2
    try:
        ignore = tomllib.loads(Path(argv[0]).read_text())["ignore-filename-regex"]
        mode, blocks = merge((Path(p).read_text().splitlines() for p in argv[2:]), ignore)
        Path(argv[1]).write_text("".join([f"{mode}\n", *(f"{b} {n}\n" for b, n in blocks.items())]))
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f"coverage_profile: {error!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
