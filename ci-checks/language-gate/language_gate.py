import json
import subprocess
import sys
from fnmatch import fnmatchcase

# A glob starting with / is rooted at the repo root; any other matches at any depth. fnmatch's * also matches /.
# Over-matching only runs checks that could have been skipped; missing a file skips checks that should run.
SHARED = ("/.github/workflows/*", "/.github/checks/*")
PATTERNS = {
    "python": (
        "*.py",
        "*.pyi",
        "pyproject.toml",
        "uv.lock",
        "uv.toml",
        "requirements*.txt",
        ".python-version",
        "setup.cfg",
        "pytest.ini",
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
        *("*.ts", "*.tsx", "*.mts", "*.cts", "*.js", "*.jsx", "*.mjs", "*.cjs"),
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        ".npmrc",
        "tsconfig*.json",
        "jsconfig*.json",
        ".nvmrc",
        ".node-version",
    ),
    "go": ("*.go", "go.mod", "go.sum", "go.work", "go.work.sum", "testdata/*"),
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: language_gate.py {{{'|'.join(PATTERNS)}}} BASE  (run from the repo root)", file=sys.stderr)
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
