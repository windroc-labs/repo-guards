"""Write prettier-files.json: the extensions and file names prettier formats, for language_gate.py.

npx prettier@<version> --support-info | python3 prettier_files.py <version>

It never reads prettier-files.json and replaces it only once the support info parsed,
so a run works whatever state the file is in and a failed run leaves it as it was.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "prettier-files.json"


def prettier_files(version: str, support_info: dict) -> dict:
    languages = support_info["languages"]
    return {
        "prettier": version,
        "extensions": sorted({extension for language in languages for extension in language.get("extensions", [])}),
        "filenames": sorted({name for language in languages for name in language.get("filenames", [])}),
    }


def write(text: str, path: Path) -> None:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            os.fchmod(file.fileno(), 0o644)  # mkstemp makes it 0600
            file.write(text)
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: npx prettier@VERSION --support-info | python3 prettier_files.py VERSION", file=sys.stderr)
        return 2
    try:
        record = prettier_files(argv[0], json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"bad prettier --support-info on stdin: {error!r}", file=sys.stderr)
        return 1
    write(json.dumps(record, indent=2) + "\n", OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
