import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

# Each list is the files the language workflow's tools read, not only its source.
# A glob starting with / is rooted at the repo root; any other matches at any depth. fnmatch's * also matches /.
# Over-matching only runs checks that could have been skipped; missing a file skips checks that should run.
SHARED = ("/.github/workflows/*", "/.github/checks/*")
# Made by `prettier-files` from the support info of the prettier that typescript/package.json pins.
PRETTIER_FILES = Path(__file__).resolve().parent / "prettier-files.json"
PRETTIER = json.loads(PRETTIER_FILES.read_text())
PATTERNS = {
    "python": (
        "*.py",
        "*.pyi",
        "*.ipynb",  # in ruff's default include
        "pyproject.toml",
        "uv.lock",
        "uv.toml",
        "*requirements*.txt",
        "requirements*.in",
        ".python-version",
        "setup.cfg",
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "tox.ini",
    ),
    "rust": (
        "*.rs",
        "Cargo.toml",
        "Cargo.lock",
        ".cargo/config",
        ".cargo/config.toml",
        "rust-toolchain",
        "rust-toolchain.toml",
        "clippy.toml",
        ".clippy.toml",
        "rustfmt.toml",
        ".rustfmt.toml",
    ),
    "typescript": (
        # Format runs prettier on the whole tree: every file prettier formats, its ignore file and .editorconfig.
        *(f"*{extension}" for extension in PRETTIER["extensions"]),
        *PRETTIER["filenames"],
        ".prettierignore",
        ".editorconfig",
        ".npmrc",
        ".nvmrc",
        ".node-version",
    ),
    "go": (
        "*.go",
        # Non-Go files go build compiles or links into a package (cgo, assembly, SWIG, .syso).
        *("*.c", "*.cc", "*.cpp", "*.cxx", "*.h", "*.hh", "*.hpp", "*.hxx", "*.m", "*.f", "*.F", "*.for", "*.f90"),
        *("*.s", "*.S", "*.sx", "*.swig", "*.swigcxx", "*.syso"),
        "go.mod",
        "go.sum",
        "go.work",
        "go.work.sum",
        "vendor/modules.txt",
        "testdata/*",
    ),
}
SHOWN = 10


class Error(Exception):
    pass


def matches(glob: str, path: str) -> bool:
    if glob.startswith("/"):
        return fnmatchcase(path, glob[1:])
    return fnmatchcase(path, glob) or fnmatchcase(path, f"*/{glob}")


def touches(language: str, paths: list[str]) -> list[str]:
    globs = PATTERNS[language] + SHARED
    return [path for path in paths if any(matches(glob, path) for glob in globs)]


def changed(base: str) -> list[str]:
    # --no-renames lists both sides of a rename, so moving a file out of a language still counts for it.
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", base, "HEAD"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise Error(f"git diff {base} HEAD failed: {result.stderr.decode(errors='replace').strip()}")
    return [name for name in result.stdout.decode(errors="surrogateescape").split("\0") if name]


def prettier_files(version: str, support_info: dict) -> dict:
    languages = support_info["languages"]
    return {
        "prettier": version,
        "extensions": sorted({extension for language in languages for extension in language.get("extensions", [])}),
        "filenames": sorted({name for language in languages for name in language.get("filenames", [])}),
    }


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "prettier-files":
        # npx prettier@<version> --support-info | python3 language_gate.py prettier-files <version>
        #   > prettier-files.json
        print(json.dumps(prettier_files(argv[1], json.load(sys.stdin)), indent=2))
        return 0
    if len(argv) != 2:
        usage = f"language_gate.py {{{'|'.join(PATTERNS)}}} BASE  (run from the repo root)"
        print(f"usage: {usage}\n       language_gate.py prettier-files VERSION < SUPPORT_INFO", file=sys.stderr)
        return 2
    language, base = argv
    if language not in PATTERNS:
        print(f"unknown language {language!r}; expected one of {', '.join(PATTERNS)}", file=sys.stderr)
        return 2
    try:
        hits = touches(language, changed(base))
    except Error as error:
        print(error, file=sys.stderr)
        return 2
    if hits:
        print(f"{len(hits)} changed file(s) for {language}, e.g.:", file=sys.stderr)
        # Quoted, so a file name holding a newline cannot start a workflow command line in the log.
        for path in hits[:SHOWN]:
            print(f"  {json.dumps(path)}", file=sys.stderr)
    else:
        print(f"no changed file for {language}: its checks are skipped", file=sys.stderr)
    print(f"run={'true' if hits else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
