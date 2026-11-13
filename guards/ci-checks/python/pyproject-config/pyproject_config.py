import json
import sys
import tomllib
from pathlib import Path
from typing import Any

USAGE = """\
usage: pyproject_config.py pin PYPROJECT PREFIX
       pyproject_config.py project-name PYPROJECT
       pyproject_config.py pyright-config PYPROJECT OUTPUT [DIR ...]"""


class Error(Exception):
    pass


def load(path: str) -> dict[str, Any]:
    with open(path, "rb") as file:
        return tomllib.load(file)


def pin(pyproject: dict[str, Any], prefix: str) -> str:
    for requirement in pyproject["dependency-groups"]["dev"]:
        if requirement.startswith(prefix):
            return requirement
    raise Error(f"no dev dependency starts with {prefix}")


def project_name(pyproject: dict[str, Any]) -> str:
    return pyproject.get("project", {}).get("name", "")


def pyright_config(pyproject: dict[str, Any], dirs: list[str]) -> dict[str, Any]:
    config = pyproject["tool"]["pyright"]
    config["extraPaths"] = [str(Path(d).resolve()) for d in dirs]
    return config


def main(argv: list[str]) -> int:
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    arity = {"pin": (2, 2), "project-name": (1, 1), "pyright-config": (2, None)}.get(command)
    if arity is None or len(args) < arity[0] or (arity[1] is not None and len(args) > arity[1]):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        pyproject = load(args[0])
        if command == "pin":
            print(pin(pyproject, args[1]))
        elif command == "project-name":
            print(project_name(pyproject))
        else:
            Path(args[1]).write_text(json.dumps(pyright_config(pyproject, args[2:])))
    except (OSError, tomllib.TOMLDecodeError, KeyError, Error) as error:
        print(f"pyproject_config: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
