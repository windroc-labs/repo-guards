import json
import os
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

HERE = Path(__file__).resolve().parent
CI_CHECKS = HERE.parent
sys.path.insert(0, str(CI_CHECKS / "config-overrides"))
from config_overrides import toml_string

SECTION = "ci-checks/inline-exemptions/settings.toml"
OVERRIDES = ".github/checks/repo-guards.toml"
TOOLS = frozenset(
    "rustc clippy rustfmt cargo-llvm-cov cargo-test sanitizer cargo-machete "
    "ruff ruff-format pyright coverage pytest unittest vulture deptry "
    "tsc eslint prettier vitest-coverage knip vitest "
    "golangci-lint go-test go-build go-mod jscpd generated".split()
)
KEY_FIELDS = ("tool", "file", "rule", "function")
FIELDS = (*KEY_FIELDS, "count", "reason")
SUBMODULE, SYMLINK = "160000", "120000"
USAGE = "usage: inline_exemptions.py [languages]  (run from the repo root)"

Key = tuple[str, str, str, str]
Scan = Callable[[str, Path, list[str]], str]


class Error(Exception):
    pass


@dataclass(frozen=True)
class Language:
    suffixes: tuple[str, ...]
    configs: tuple[str, ...]
    directory: Path


LANGUAGES = {
    "rust": Language((".rs",), ("Cargo.toml",), CI_CHECKS / "rust/inline-exemptions"),
    "python": Language((".py", ".pyi"), ("pyproject.toml",), CI_CHECKS / "python/inline-exemptions"),
    "go": Language((".go",), ("go.mod",), CI_CHECKS / "go/inline-exemptions"),
    "typescript": Language(
        (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
        (),
        CI_CHECKS / "typescript/inline-exemptions",
    ),
}


def language_of(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    for language, spec in LANGUAGES.items():
        if name in spec.configs or name.endswith(spec.suffixes):
            return language
    return None


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise Error("git not found") from error
    if result.returncode != 0:
        raise Error(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def tracked(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Files per language, and the tracked symlinks among them."""
    files: dict[str, set[str]] = {}
    symlinks: set[str] = set()
    for record in git(root, "ls-files", "-s", "-z").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode = meta.split(" ", 1)[0]
        language = language_of(path)
        if mode == SUBMODULE or language is None:
            continue
        if mode == SYMLINK:
            symlinks.add(path)
        elif (root / path).is_file():
            files.setdefault(language, set()).add(path)
    return {language: sorted(paths) for language, paths in files.items()}, sorted(symlinks)


def languages(root: Path) -> dict[str, bool]:
    files, symlinks = tracked(root)
    present = set(files) | {language_of(path) for path in symlinks}
    return {language: language in present for language in LANGUAGES}


def command(language: str, root: Path) -> tuple[list[str], Path, dict[str, str]]:
    directory = LANGUAGES[language].directory
    env = dict(os.environ)
    if language == "python":
        return [sys.executable, str(directory / "exemptions.py"), str(root)], directory, env
    if language == "rust":
        # Cargo and rustup read config from the working directory up: run from / (root-owned), outside
        # the caller's tree and any shared temp directory.
        if "RUSTUP_TOOLCHAIN" not in env:
            toolchain = tomllib.loads((CI_CHECKS / "rust/rust-toolchain.toml").read_text(encoding="utf-8"))
            env["RUSTUP_TOOLCHAIN"] = toolchain["toolchain"]["channel"]
        manifest = str(directory / "Cargo.toml")
        argv = ["cargo", "run", "--release", "--locked", "--quiet", "--manifest-path", manifest, "--", str(root)]
        return argv, Path("/"), env
    if language == "go":
        env["GOWORK"] = "off"
        return ["go", "run", ".", str(root)], directory, env
    return ["node", "inline-exemptions.ts", str(root)], directory, env


def run_scanner(
    language: str, root: Path, files: list[str], runner: Callable[..., Any] = subprocess.run
) -> str:
    argv, cwd, env = command(language, root)
    steps = [(argv, None)]
    if language == "typescript" and not (cwd / "node_modules").is_dir():
        steps.insert(0, (["npm", "ci", "--no-audit", "--no-fund", "--ignore-scripts"], subprocess.DEVNULL))
    result = None
    for step, stdout in steps:
        try:
            result = runner(
                step,
                cwd=cwd,
                env=env,
                input="\0".join(files) if step is argv else None,
                stdout=stdout if stdout is not None else subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except FileNotFoundError as error:
            raise Error(f"{language} scanner needs {step[0]}, which is not installed") from error
        if result.returncode != 0:
            last = (result.stderr or "").strip().splitlines()[-1:] or [""]
            raise Error(f"{language} scanner: {step[0]} exited with {result.returncode}: {last[0]}")
    return result.stdout


def parse_output(language: str, text: str) -> tuple[list[Key], list[tuple[str, str]]]:
    found: list[Key] = []
    errors: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise Error(f"{language} scanner wrote a line that is not JSON: {line[:200]}") from error
        if not isinstance(record, dict):
            raise Error(f"{language} scanner wrote a record that is not an object: {line[:200]}")
        if set(record) == {"file", "error"} and all(isinstance(v, str) for v in record.values()):
            errors.append((record["file"], record["error"]))
        elif set(record) == set(KEY_FIELDS) and all(isinstance(v, str) for v in record.values()):
            if record["tool"] not in TOOLS:
                raise Error(f"{language} scanner reported an unknown tool {toml_string(record['tool'])}")
            found.append(tuple(record[f] for f in KEY_FIELDS))
        else:
            raise Error(f"{language} scanner wrote a malformed record: {line[:200]}")
    return found, errors


def describe(index: int, item: Any) -> str:
    if not isinstance(item, dict):
        return f"exemption #{index}"
    shown = ", ".join(f"{f} = {toml_string(str(item[f]))}" for f in KEY_FIELDS if f in item)
    return f"exemption #{index} ({shown})"


def entry_problem(item: Any) -> str | None:
    if not isinstance(item, dict):
        return "is not a table"
    unknown = sorted(set(item) - set(FIELDS))
    if unknown:
        return "has unknown field " + ", ".join(toml_string(f) for f in unknown)
    missing = [f for f in FIELDS if f not in item]
    if missing:
        return "is missing field " + ", ".join(toml_string(f) for f in missing)
    for field in (*KEY_FIELDS, "reason"):
        if not isinstance(item[field], str):
            return f"field {toml_string(field)} is not a string"
    if item["tool"] not in TOOLS:
        return f"tool {toml_string(item['tool'])} is not a built-in tool"
    count = item["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return "count is not a positive integer"
    if not item["reason"].strip():
        return "reason is empty"
    return None


def load_settings(path: Path) -> tuple[set[str], dict[Key, int], list[str]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return set(), {}, [f"settings {toml_string(str(path))}: {toml_string(str(error))}"]
    problems = [f"settings key {toml_string(k)} is unknown" for k in sorted(set(data) - {"skip", "exemption"})]
    skip = data.get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(t, str) for t in skip):
        problems.append("skip is not a list of strings")
        skip = []
    problems += [f"skip names {toml_string(t)}, which is not a built-in tool" for t in skip if t not in TOOLS]
    items = data.get("exemption", [])
    if not isinstance(items, list):
        return set(skip), {}, [*problems, "exemption is not a list"]
    allowlist: dict[Key, int] = {}
    first: dict[Key, int] = {}
    for index, item in enumerate(items, 1):
        problem = entry_problem(item)
        if problem is None:
            key = tuple(item[f] for f in KEY_FIELDS)
            if key in first:
                problem = f"has the same key as exemption #{first[key]}"
            else:
                first[key] = index
                allowlist[key] = item["count"]
        if problem:
            problems.append(f"{describe(index, item)} {problem}")
    return set(skip), allowlist, problems


def key_lines(key: Key) -> list[str]:
    return [f"{field} = {toml_string(value)}" for field, value in zip(KEY_FIELDS, key)]


def report(found: Counter, allowlist: dict[Key, int]) -> list[str]:
    def order(key: Key) -> tuple[str, str, str, str]:
        tool, file, rule, function = key
        return file, function, tool, rule

    lines: list[str] = []
    added = removed = changed = 0
    for key in sorted(set(found) | set(allowlist), key=order):
        code, listed = found.get(key, 0), allowlist.get(key, 0)
        if code == listed:
            continue
        if not listed:
            added += 1
            lines += ["# add: fill in reason", f'[["{SECTION}".exemption]]', *key_lines(key), f"count = {code}", 'reason = ""', ""]
        elif not code:
            removed += 1
            lines += ["# remove: no longer in the code", *key_lines(key), ""]
        else:
            changed += 1
            lines += [f"# count: {listed} in the allowlist, {code} in the code", *key_lines(key), f"count = {code}", ""]
    if not lines:
        return []
    head = [
        f"Inline exemptions: the allowlist in {OVERRIDES} does not match the code.",
        f'Edit its ["{SECTION}"] section: add the "add" entries with a reason, delete the "remove" ones, set each "count" one to the new count.',
        "",
    ]
    return [*head, *lines, f"{added} to add, {removed} to remove, {changed} to change count."]


def check(root: Path, settings_path: Path, scan: Scan, out: TextIO) -> int:
    def say(*lines: str) -> None:
        for line in lines:
            print(line, file=out)

    try:
        if git(root, "status", "--porcelain", "-z", "--untracked-files=no"):
            say("Note: uncommitted changes to tracked files; checking the work tree as it is.")
        files, symlinks = tracked(root)
    except Error as error:
        say(f"error: {toml_string(str(error))}")
        return 2
    skip, allowlist, problems = load_settings(settings_path)
    errors = [f"allowlist: {p}" for p in problems]
    errors += [f"file {toml_string(path)}: is a symlink" for path in symlinks]
    found: Counter = Counter()
    for language, paths in files.items():
        try:
            keys, failures = parse_output(language, scan(language, root, paths))
        except Error as error:
            errors.append(toml_string(str(error)))
            continue
        errors += [f"file {toml_string(path)}: {toml_string(message)}" for path, message in failures]
        found.update(key for key in keys if key[0] not in skip)
    if errors:
        say(*(f"error: {e}" for e in errors))
        return 2
    lines = report(found, allowlist)
    if lines:
        say(*lines)
        return 1
    say(f"Inline exemptions: {sum(found.values())} exemptions, all in the allowlist.")
    return 0


def main(argv: list[str]) -> int:
    root = Path.cwd()
    if argv == ["languages"]:
        try:
            present = languages(root)
        except Error as error:
            print(f"error: {toml_string(str(error))}")
            return 2
        for language, used in present.items():
            print(f"{language}={'true' if used else 'false'}")
        return 0
    if argv:
        print(USAGE, file=sys.stderr)
        return 2
    return check(root, HERE / "settings.toml", run_scanner, sys.stdout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
