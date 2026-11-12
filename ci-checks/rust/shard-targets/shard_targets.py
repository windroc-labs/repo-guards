import json
import sys


def targets(shards: list[str]) -> list[str]:
    found: list[str] = []
    for shard in shards:
        words = shard.split()
        found += words[: words.index("--")] if "--" in words else words
    # cargo-llvm-cov rejects a flag such as --lib given twice.
    return list(dict.fromkeys(found))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: shard_targets.py SHARDS_JSON", file=sys.stderr)
        return 2
    print(*targets(json.loads(argv[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
