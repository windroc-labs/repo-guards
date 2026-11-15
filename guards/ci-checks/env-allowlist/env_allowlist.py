import bisect
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, NamedTuple

USAGE = "usage: env_allowlist.py rust CONFIG"
SECTION = '["ci-checks/env-allowlist/allowlist.toml"] in .github/checks/repo-guards.toml (an owner-approved change).'
HINT = f"Read only allowlisted variables, or add the name to allow under {SECTION}"
ADOPT_HINT = f"To adopt the check: hard-code every name and merge that code, then set allow under {SECTION}"
NOT_LITERAL = "name is not a string literal"
FUNCTIONS = {"var", "var_os", "vars", "vars_os"}
READS_ALL = {"vars", "vars_os"}
MACROS = {"env", "option_env"}
OPEN = {"(": ")", "[": "]", "{": "}"}
RAW_STRING = re.compile(r'[bc]?r(#*)"')
STRING = re.compile(r'[bc]?"')


class Error(Exception):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int
    column: int
    start: int


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    message: str


def is_ident_start(c: str) -> bool:
    return c == "_" or c.isalpha()


def is_ident(c: str) -> bool:
    return c == "_" or c.isalnum()


def lex(text: str) -> list[Token]:
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    tokens: list[Token] = []
    n = len(text)

    def unterminated(at: int, what: str) -> Error:
        return Error(f"line {bisect.bisect_right(line_starts, at)}: unterminated {what}")

    def push(kind: str, value: str, start: int) -> None:
        line = bisect.bisect_right(line_starts, start)
        tokens.append(Token(kind, value, line, start - line_starts[line - 1] + 1, start))

    def ident_end(i: int) -> int:
        while i < n and is_ident(text[i]):
            i += 1
        return i

    i = 0
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while depth:
                if j >= n:
                    raise unterminated(i, "block comment")
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            i = j
        elif raw := RAW_STRING.match(text, i):
            close = '"' + raw.group(1)
            end = text.find(close, raw.end())
            if end < 0:
                raise unterminated(i, "raw string")
            push("str", text[raw.end() : end], i)
            i = end + len(close)
        elif plain := STRING.match(text, i):
            j = plain.end()
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise unterminated(i, "string")
            push("str", text[plain.end() : j], i)
            i = j + 1
        elif c == "r" and text.startswith("r#", i) and i + 2 < n and is_ident_start(text[i + 2]):
            end = ident_end(i + 2)
            push("ident", text[i + 2 : end], i)
            i = end
        elif c == "'" or (c == "b" and text.startswith("b'", i)):
            q = i if c == "'" else i + 1
            if text.startswith("\\", q + 1):
                end = text.find("'", q + 3)
                if end < 0:
                    raise unterminated(i, "char literal")
                i = end + 1
            elif q + 2 < n and text[q + 2] == "'":
                i = q + 3
            else:
                i = ident_end(q + 1)
            push("other", text[q:i], q)
        elif is_ident_start(c):
            end = ident_end(i)
            push("ident", text[i:end], i)
            i = end
        elif c.isdigit():
            end = ident_end(i)
            push("other", text[i:end], i)
            i = end
        elif text.startswith("::", i):
            push("punct", "::", i)
            i += 2
        else:
            push("punct", c, i)
            i += 1
    return tokens


def close(tokens: list[Token], open_at: int) -> int | None:
    opener = tokens[open_at].text
    closer, depth = OPEN[opener], 0
    for i in range(open_at, len(tokens)):
        if tokens[i].kind == "punct" and tokens[i].text == opener:
            depth += 1
        elif tokens[i].kind == "punct" and tokens[i].text == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def texts(tokens: list[Token], i: int, words: list[str]) -> bool:
    return [t.text for t in tokens[i : i + len(words)]] == words


def test_module_end(tokens: list[Token], attribute: int) -> int | None:
    i = attribute + 7
    while texts(tokens, i, ["#", "["]):
        end = close(tokens, i + 1)
        if end is None:
            return None
        i = end + 1
    if texts(tokens, i, ["pub"]):
        i += 1
        if texts(tokens, i, ["("]):
            end = close(tokens, i)
            if end is None:
                return None
            i = end + 1
    named = i + 1 < len(tokens) and tokens[i + 1].kind == "ident"
    if texts(tokens, i, ["mod"]) and named and texts(tokens, i + 2, ["{"]):
        return close(tokens, i + 2)
    return None


def release_tokens(tokens: list[Token]) -> list[Token]:
    kept: list[Token] = []
    i = 0
    while i < len(tokens):
        end = None
        if texts(tokens, i, ["#", "[", "cfg", "(", "test", ")", "]"]):
            end = test_module_end(tokens, i)
        if end is None:
            kept.append(tokens[i])
            i += 1
        else:
            i = end + 1
    return kept


Leaf = tuple[list[str], str | None]


def use_tree(tokens: list[Token], i: int, end: int, prefix: list[str]) -> tuple[list[Leaf], int]:
    segments = list(prefix)
    if i < end and tokens[i].text == "::":
        i += 1
    while i < end:
        token = tokens[i]
        if token.text == "{":
            leaves: list[Leaf] = []
            i += 1
            while i < end and tokens[i].text != "}":
                found, i = use_tree(tokens, i, end, segments)
                leaves += found
                if i < end and tokens[i].text == ",":
                    i += 1
            return leaves, i + 1
        if token.text == "*":
            return [(segments + ["*"], None)], i + 1
        if token.kind != "ident":
            return [], i + 1
        segments.append(token.text)
        i += 1
        if i < end and tokens[i].text == "::":
            i += 1
        elif i + 1 < end and tokens[i].text == "as":
            return [(segments, tokens[i + 1].text)], i + 2
        else:
            return [(segments, None)], i
    return [], i


def import_findings(segments: list[str], rename: str | None) -> list[str]:
    if segments[-1] == "self":
        segments = segments[:-1]
    if len(segments) >= 2 and segments[-2] == "env" and segments[-1] in FUNCTIONS:
        return [f"import of env::{segments[-1]}; call it through the module as env::{segments[-1]}"]
    if len(segments) >= 2 and segments[-2] == "env" and segments[-1] == "*":
        return ["import of env::*; call each function through the module as env::<function>"]
    if segments[0] in {"std", "core"} and segments[-1] in MACROS and rename not in {None, segments[-1]}:
        return [f"renamed import of {'::'.join(segments)}; import it as {segments[-1]}"]
    return []


def literal_name(tokens: list[Token], i: int) -> str | None:
    if i + 1 < len(tokens) and tokens[i].kind == "str" and tokens[i + 1].text in {",", ")", "]", "}"}:
        return tokens[i].text
    return None


def findings(path: str, tokens: list[Token], allow: frozenset[str] | None) -> list[Finding]:
    found: list[Finding] = []

    def report(token: Token, message: str) -> None:
        found.append(Finding(path, token.line, token.column, message))

    def read(token: Token, name: str | None) -> None:
        if name is None:
            report(token, NOT_LITERAL)
        elif allow is None or name not in allow:
            report(token, f"env var {name} is not in the allowlist")

    in_use: set[int] = set()
    for i, token in enumerate(tokens):
        if token.kind != "ident" or token.text != "use" or texts(tokens, i + 1, ["<"]):
            continue
        end = next((j for j in range(i + 1, len(tokens)) if tokens[j].text == ";"), len(tokens))
        in_use.update(range(i, end))
        leaves, _ = use_tree(tokens, i + 1, end, [])
        for segments, rename in leaves:
            for message in import_findings(segments, rename):
                report(token, message)

    for i, token in enumerate(tokens):
        if token.kind != "ident" or i in in_use:
            continue
        if token.text in MACROS and texts(tokens, i + 1, ["!"]) and i + 2 < len(tokens) and tokens[i + 2].text in OPEN:
            read(token, literal_name(tokens, i + 3))
        elif token.text in FUNCTIONS and i >= 2 and tokens[i - 1].text == "::" and tokens[i - 2].text == "env":
            if token.text in READS_ALL:
                report(token, f'env::{token.text} reads every variable; read each name with env::var("NAME")')
                continue
            j = i + 1
            if texts(tokens, j, ["::", "<"]):
                depth, j = 0, j + 1
                while j < len(tokens):
                    depth += {"<": 1, ">": -1}.get(tokens[j].text, 0)
                    j += 1
                    if depth == 0:
                        break
            read(token, literal_name(tokens, j + 1) if texts(tokens, j, ["("]) else None)
    return sorted(found, key=lambda f: (f.line, f.column))


class Language(NamedTuple):
    pathspec: str
    scan: Callable[[str, str, frozenset[str] | None], list[Finding]]


def scan_rust(path: str, text: str, allow: frozenset[str] | None) -> list[Finding]:
    return findings(path, release_tokens(lex(text)), allow)


LANGUAGES = {"rust": Language("*.rs", scan_rust)}


def strings(value: Any, key: str, config: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Error(f"{config}: `{key}` must be a list of strings")
    return value


def matches(glob: str, path: str) -> bool:
    if glob.startswith("/"):
        return fnmatchcase(path, glob[1:])
    return fnmatchcase(path, glob) or fnmatchcase(path, f"*/{glob}")


def tracked(pathspec: str) -> list[str]:
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "ls-files", "-z", "--", pathspec]
    try:
        done = subprocess.run(command, capture_output=True, text=True)
    except OSError as error:
        raise Error(f"git ls-files failed: {error}") from error
    if done.returncode != 0:
        raise Error(f"git ls-files failed: {done.stderr.strip()}")
    return sorted(p for p in done.stdout.split("\0") if p and Path(p).is_file())


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def check(name: str, config: Path) -> int:
    language = LANGUAGES[name]
    try:
        settings = tomllib.loads(config.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Error(f"{config}: {error}") from error
    # A config without `allow` is a project that has not adopted the check: report, never fail.
    allow = None if "allow" not in settings else frozenset(strings(settings["allow"], "allow", config))
    table = settings.get(name)
    test_paths = strings(table.get("test_paths") if isinstance(table, dict) else None, f"{name}.test_paths", config)
    files = [p for p in tracked(language.pathspec) if not any(matches(g, p) for g in test_paths)]
    found: list[Finding] = []
    for path in files:
        try:
            found += language.scan(path, Path(path).read_text(encoding="utf-8"), allow)
        except (OSError, UnicodeDecodeError, Error) as error:
            raise Error(f"{path}: {error}") from error
    checked = plural(len(files), "file")
    if not found:
        if allow is None:
            print(f"ok env allowlist (not adopted): {checked} checked")
        else:
            listed = ", ".join(json.dumps(n) for n in sorted(allow))
            print(f"ok env allowlist: {checked} checked, allow=[{listed}]")
        return 0
    status = "FAIL env allowlist" if allow is not None else "report env allowlist (not adopted, CI does not fail)"
    print(f"{status}: {plural(len(found), 'finding')} in {len({f.path for f in found})} of {checked}")
    for f in found:
        print(f"  {f.path}:{f.line}:{f.column}: {f.message}")
    if allow is None:
        print(ADOPT_HINT)
        return 0
    print(HINT)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in LANGUAGES:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return check(argv[0], Path(argv[1]))
    except Error as error:
        print(f"env-allowlist: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
