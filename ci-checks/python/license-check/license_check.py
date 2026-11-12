import json
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "allowlist"))
from allowlist import Allowlist, Error, load, pep503

UNKNOWN = "UNKNOWN"


class Malformed(Exception):
    pass


def normalize(license: str) -> str:
    return " ".join(license.split()).casefold()


def satisfied(expression: str, allowed: set[str]) -> bool:
    """SPDX expression semantics: OR needs one side allowed, AND needs every side; AND binds tighter."""
    allow = {normalize(a) for a in allowed}
    tokens = re.findall(r"[()]|[^\s()]+", expression)
    pos = 0

    def peek() -> str:
        return tokens[pos].upper() if pos < len(tokens) else ""

    def take() -> str:
        nonlocal pos
        if pos == len(tokens):
            raise Malformed
        pos += 1
        return tokens[pos - 1]

    def license_id() -> str:
        token = take()
        if token in ("(", ")") or token.upper() in ("AND", "OR", "WITH"):
            raise Malformed
        return token

    def term() -> bool:
        if peek() == "(":
            take()
            value = either()
            if take() != ")":
                raise Malformed
            return value
        name = license_id()
        if peek() == "WITH":
            take()
            name += " WITH " + license_id()
        return normalize(name) in allow

    def chain(operator: str, operand: Callable[[], bool]) -> list[bool]:
        values = [operand()]
        while peek() == operator:
            take()
            values.append(operand())
        return values

    def both() -> bool:
        return all(chain("AND", term))

    def either() -> bool:
        return any(chain("OR", both))

    try:
        value = either()
    except Malformed:
        return False
    return value and pos == len(tokens)


def alternatives(row: dict[str, str]) -> list[str]:
    """pip-licenses --from mixed's pick: the expression, else any one classifier, else the metadata whole."""
    if row["License-Expression"] != UNKNOWN:
        return [row["License-Expression"]]
    if row["License-Classifier"] != UNKNOWN:
        return row["License-Classifier"].split("; ")
    return [row["License-Metadata"]]


def exceptions(allowlist: Allowlist, row: dict[str, str]) -> set[str]:
    """Licenses the project allowlist's licenses.exceptions add for this package."""
    return {
        name
        for exception in allowlist.licenses
        if exception.spec.matches(row["Name"], row["Version"], pep503)
        for name in exception.allow
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: license_check.py LICENSES_TOML PIP_LICENSES_JSON [ALLOWLIST_TOML]", file=sys.stderr)
        return 2
    try:
        config = tomllib.loads(Path(argv[0]).read_text())
        allowed: set[str] = set(config["allow"])
        classifiers: dict[str, str] = config["python-classifiers"]
        rows: list[dict[str, str]] = json.loads(Path(argv[1]).read_text())
        allowlist = load(Path(argv[2])) if len(argv) == 3 else Allowlist()
        denied: list[str] = []
        for row in rows:
            licenses = alternatives(row)
            permitted = allowed | exceptions(allowlist, row)
            if not any(satisfied(classifiers.get(alt, alt), permitted) for alt in licenses):
                denied.append(f"{row['Name']} {row['Version']}: {'; '.join(licenses)}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Error) as error:
        print(f"license_check: {error!r}", file=sys.stderr)
        return 2
    for line in denied:
        print(line, file=sys.stderr)
    if denied:
        print(
            f"{len(denied)} package(s) under a license that licenses.toml's allow does not satisfy;"
            " a licenses.exceptions entry in the project allowlist admits one",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
