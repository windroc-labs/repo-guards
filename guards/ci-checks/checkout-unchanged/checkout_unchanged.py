"""After a job's checks, the caller's checkout must be as git checked it out: git status lists nothing."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config-overrides"))
import config_overrides  # noqa: E402

OVERRIDES = config_overrides.OVERRIDES
SECTION = "ci-checks/checkout-unchanged/settings.toml"
SETTINGS = Path(__file__).resolve().parent / "settings.toml"
# --untracked-files=all lists every file: without it a directory of ignored files is one line, hiding later additions.
STATUS = ("status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=all")
# Dependencies the workflow itself installs in the checkout, since node resolves them from there.
INSTALL_DIR = "node_modules"
SHOWN = 50
USAGE = """usage: checkout_unchanged.py installed REPO FILE
       checkout_unchanged.py check REPO [FILE]"""
HINT = (
    "The checks must leave the checkout as git checked it out (?? untracked, !! ignored, the rest as git status):"
    " write build output, caches and result files outside it."
)
ADOPT_HINT = f'To adopt: clear the paths above, then set `enforce = true` under ["{SECTION}"] in {OVERRIDES}.'

# A git status entry: its two-letter code and its path.
Entry = tuple[str, str]


class Error(Exception):
    pass


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", str(repo), "-c", "core.fsmonitor=false", "--no-optional-locks", *args]
    try:
        return subprocess.run(command, capture_output=True)
    except OSError as error:
        raise Error(f"git {args[0]} failed: {error}") from error


def status(repo: Path) -> bytes:
    done = git(repo, *STATUS)
    if done.returncode != 0:
        raise Error(f"git status failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def entries(raw: bytes) -> set[Entry]:
    fields = iter(raw.decode("utf-8", "surrogateescape").split("\0"))
    found = set()
    for field in fields:
        code, path = field[:2], field[3:]
        if not path:
            continue
        if "R" in code or "C" in code:
            # The path it was renamed or copied from follows as a field of its own.
            next(fields, None)
        found.add((code, path))
    return found


def installed(file: Path | None) -> frozenset[Entry]:
    """The untracked and ignored paths under an install directory that FILE recorded; none without FILE."""
    if file is None:
        return frozenset()
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return frozenset()
    except OSError as error:
        raise Error(f"{file}: {error}") from error
    return frozenset(e for e in entries(raw) if e[0] in ("??", "!!") and INSTALL_DIR in e[1].split("/"))


def enforce(repo: Path) -> bool:
    """The setting at HEAD over the default: the work tree's copy is what the checks could have rewritten."""
    try:
        settings = tomllib.loads(SETTINGS.read_text(encoding="utf-8"))
        shown = git(repo, "show", f"HEAD:{OVERRIDES}")
        overrides = config_overrides.parse_overrides(shown.stdout.decode("utf-8")) if shown.returncode == 0 else {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise Error(f"{SETTINGS}: {error}") from error
    except UnicodeDecodeError as error:
        raise Error(f"{OVERRIDES}: {error}") from error
    except config_overrides.Error as error:
        raise Error(str(error)) from error
    section = overrides.get(SECTION, {})
    if not isinstance(section, dict):
        raise Error(f'{OVERRIDES}: ["{SECTION}"] is not a table')
    value = config_overrides.merge(settings, section).get("enforce")
    if not isinstance(value, bool):
        raise Error(f'["{SECTION}"]: `enforce` must be true or false')
    return value


def check(repo: Path, file: Path | None) -> int:
    adopted = enforce(repo)
    changed = sorted(entries(status(repo)) - installed(file), key=lambda entry: (entry[1], entry[0]))
    if not changed:
        print("ok checkout unchanged" if adopted else "ok checkout unchanged (not adopted)")
        return 0
    head = "FAIL checkout unchanged" if adopted else "report checkout unchanged (not adopted, CI does not fail)"
    print(f"{head}: {len(changed)} path{'' if len(changed) == 1 else 's'} changed or added since checkout")
    # A path is the PR's text: as a JSON string after the code it cannot start a workflow command or a new line.
    for code, path in changed[:SHOWN]:
        print(f"  {code} {json.dumps(path)}")
    if len(changed) > SHOWN:
        print(f"  ... and {len(changed) - SHOWN} more")
    print(HINT if adopted else ADOPT_HINT)
    return 1 if adopted else 0


def record(repo: Path, file: Path) -> int:
    raw = status(repo)
    try:
        file.write_bytes(raw)
    except OSError as error:
        raise Error(f"{file}: {error}") from error
    return 0


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[0] == "installed":
            return record(Path(argv[1]), Path(argv[2]))
        if len(argv) in (2, 3) and argv[0] == "check":
            return check(Path(argv[1]), Path(argv[2]) if len(argv) == 3 else None)
    except Error as error:
        print(f"checkout-unchanged: {error}", file=sys.stderr)
        return 2
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
