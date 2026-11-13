import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coverage"))
from shard_artifact import digest  # noqa: E402


def name(language: str, working_directory: str) -> str:
    return f"{language}-api-snapshot-{digest(working_directory)}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: snapshot_artifact.py LANGUAGE WORKING_DIRECTORY", file=sys.stderr)
        return 2
    print(name(*argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
