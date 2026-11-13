import hashlib
import sys


def digest(working_directory: str) -> str:
    # download-artifact runs decodeURIComponent on the name and matches it as a glob, so a hash stands in for the path.
    return hashlib.sha256(working_directory.encode()).hexdigest()[:16]


def name(language: str, working_directory: str) -> str:
    return f"{language}-coverage-{digest(working_directory)}-shard"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: shard_artifact.py LANGUAGE WORKING_DIRECTORY", file=sys.stderr)
        return 2
    print(name(*argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
