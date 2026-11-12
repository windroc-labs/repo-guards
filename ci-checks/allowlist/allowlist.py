import argparse
import re
import sys
import tomllib
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

KEYS = {
    ("bans", "allow"),
    ("bans", "build", "allow-build-scripts"),
    ("sources", "allow-git"),
    ("sources", "allow-registry"),
    ("licenses", "exceptions"),
}


class Error(Exception):
    pass


def same(name: str) -> str:
    return name


def pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Spec(NamedTuple):
    name: str
    version: str | None = None

    def matches(self, name: str, version: str, canonical: Callable[[str], str] = same) -> bool:
        return canonical(self.name) == canonical(name) and self.version in (None, version)

    def __str__(self) -> str:
        return self.name if self.version is None else f"{self.name}@{self.version}"


class LicenseException(NamedTuple):
    spec: Spec
    allow: tuple[str, ...]


class Locked(NamedTuple):
    name: str
    version: str
    kind: str
    url: str


@dataclass(frozen=True)
class Allowlist:
    packages: tuple[Spec, ...] = ()
    build: tuple[Spec, ...] = ()
    git: tuple[str, ...] = ()
    registries: tuple[str, ...] = ()
    licenses: tuple[LicenseException, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


def leaves(
    table: dict[str, Any], path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    for key, value in table.items():
        if isinstance(value, dict):
            yield from leaves(value, path + (key,))
        else:
            yield path + (key,), value


def parse_spec(text: str, where: str) -> Spec:
    scope = "@" if text.startswith("@") else ""
    name, at, version = text.removeprefix(scope).partition("@")
    name = scope + name
    if name in ("", "@") or (at and not version):
        raise Error(f"{where}: {text!r} is not a name or name@version")
    return Spec(name, None if version in ("", "*") else version)


def to_spec(entry: Any, where: str) -> Spec:
    if isinstance(entry, str):
        return parse_spec(entry, where)
    if isinstance(entry, dict):
        if isinstance(crate := entry.get("crate"), str):
            return parse_spec(crate, where)
        name, version = entry.get("name"), entry.get("version")
        if isinstance(name, str) and name and (version is None or isinstance(version, str)):
            return Spec(name, None if version in (None, "*") else version)
    raise Error(f'{where}: {entry!r} needs a name: "name", "name@version" or {{ crate = ... }}')


def strings(entries: list[Any], where: str) -> tuple[str, ...]:
    if not all(isinstance(e, str) and e for e in entries):
        raise Error(f"{where}: entries must be non-empty strings")
    return tuple(entries)


def license_exception(entry: Any, where: str) -> LicenseException:
    if not isinstance(entry, dict):
        raise Error(f"{where}: {entry!r} must be a table {{ crate = ..., allow = [...] }}")
    allow = entry.get("allow")
    if not isinstance(allow, list) or not allow:
        raise Error(f"{where}: {entry!r} needs a non-empty allow array")
    return LicenseException(to_spec(entry, where), strings(allow, where))


def load(path: Path) -> Allowlist:
    """Absent file: no exceptions, the strictest rules."""
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return Allowlist()
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Error(f"{path}: {error}") from error
    found: dict[tuple[str, ...], list[Any]] = {}
    for key, value in leaves(raw):
        name = ".".join(key)
        if key not in KEYS:
            raise Error(
                f"{name}: not an allowlist key; allowed: {', '.join(sorted('.'.join(k) for k in KEYS))}"
            )
        if not isinstance(value, list):
            raise Error(f"{name}: must be an array")
        found[key] = value

    def entries(*key: str) -> list[Any]:
        return found.get(key, [])

    return Allowlist(
        packages=tuple(to_spec(e, "bans.allow") for e in entries("bans", "allow")),
        build=tuple(
            to_spec(e, "bans.build.allow-build-scripts")
            for e in entries("bans", "build", "allow-build-scripts")
        ),
        git=strings(entries("sources", "allow-git"), "sources.allow-git"),
        registries=strings(entries("sources", "allow-registry"), "sources.allow-registry"),
        licenses=tuple(
            license_exception(e, "licenses.exceptions") for e in entries("licenses", "exceptions")
        ),
        raw=raw,
    )


def normalize_git(url: str) -> str:
    """host/path of a git URL, whatever its scheme, user, .git suffix, revision or trailing slash."""
    url = url.strip().removeprefix("git+").split("#", 1)[0]
    scp = re.fullmatch(r"[\w.-]+@([^:/]+):(.*)", url)
    if scp and "://" not in url:
        host, path = scp.group(1), scp.group(2)
    else:
        parts = urllib.parse.urlsplit(url)
        host, path = parts.netloc.rpartition("@")[2], parts.path
    path = path.strip("/")
    if "@" in path:
        path = path.rpartition("@")[0]
    return f"{host.lower()}/{path.removesuffix('.git').strip('/')}"


def under(url: str, prefixes: Iterable[str]) -> bool:
    url = url.rstrip("/")
    return any(url == p.rstrip("/") or url.startswith(p.rstrip("/") + "/") for p in prefixes)


def violations(
    locked: Iterable[Locked],
    allowlist: Allowlist,
    default_registry: str,
    canonical: Callable[[str], str] = same,
) -> list[str]:
    """One line per package the rules reject, naming the allowlist entry that would admit it."""
    git = {normalize_git(u) for u in allowlist.git}
    found: list[str] = []
    for package in locked:
        if package.kind == "local":
            continue
        label = f"{package.name}@{package.version}" if package.version else package.name
        if package.kind == "registry" and not under(
            package.url, [default_registry, *allowlist.registries]
        ):
            found.append(
                f"{label}: registry {package.url} is not allowed; add it to sources.allow-registry"
            )
        if package.kind == "git" and normalize_git(package.url) not in git:
            found.append(
                f"{label}: git source {package.url} is not allowed; "
                f"add https://{normalize_git(package.url)} to sources.allow-git"
            )
        if allowlist.packages and not any(
            s.matches(package.name, package.version, canonical) for s in allowlist.packages
        ):
            found.append(f'{label}: not in bans.allow; add "{label}"')
    return found


PYPI = "https://pypi.org/simple"
REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:==\s*([^\s;\\]+)|@\s*(\S+))"
)


def read_uv_lock(path: Path) -> list[Locked]:
    locked: list[Locked] = []
    for package in tomllib.loads(path.read_text()).get("package", []):
        source: dict[str, str] = package.get("source", {})
        kind = next((k for k in ("registry", "git", "url") if k in source), "local")
        locked.append(
            Locked(package["name"], package.get("version", ""), kind, source.get(kind, ""))
        )
    return locked


def read_requirements(path: Path) -> list[Locked]:
    """uv pip compile output with --emit-index-annotation: each registry package's index is its `# from` line."""
    locked: list[Locked] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            index = re.fullmatch(r"#\s*from\s+(\S+)", line)
            if index and locked and locked[-1].kind == "registry" and not locked[-1].url:
                locked[-1] = locked[-1]._replace(url=index.group(1))
        elif line.startswith(("-e ", "--editable ")):
            locked.append(Locked(Path(line.split(maxsplit=1)[1]).name, "", "local", ""))
        elif line and not raw[0].isspace() and not line.startswith("-"):
            match = REQUIREMENT.match(line)
            if not match:
                raise Error(f"{path}: cannot read requirement {line!r}")
            name, version, url = match.groups()
            if version:
                locked.append(Locked(name, version, "registry", ""))
            else:
                kind = (
                    "git"
                    if url.startswith("git+")
                    else "local"
                    if url.startswith("file:")
                    else "url"
                )
                locked.append(Locked(name, "", kind, "" if kind == "local" else url))
    return locked


def report(found: list[str]) -> int:
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(
            f"{len(found)} problem(s); each line names the allowlist entry that admits the package",
            file=sys.stderr,
        )
        return 1
    return 0


def check_python(args: argparse.Namespace) -> int:
    allowlist = load(args.allowlist)
    locked = read_uv_lock(args.uv_lock) if args.uv_lock else read_requirements(args.requirements)
    if args.pinned_out:
        pins = (f"{p.name}=={p.version}\n" for p in locked if p.kind == "registry" and p.version)
        args.pinned_out.write_text("".join(pins))
    return report(violations(locked, allowlist, PYPI, pep503))


def env_python(args: argparse.Namespace) -> int:
    names = dict.fromkeys(spec.name for spec in load(args.allowlist).build)
    if names:
        print(f"UV_NO_BINARY_PACKAGE={' '.join(names)}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="allowlist.py", description="Apply the project dependency allowlist."
    )
    commands = root.add_subparsers(required=True)
    check = commands.add_parser(
        "check", help="check locked packages against the dependency rules"
    ).add_subparsers(required=True)
    python = check.add_parser("python")
    python.add_argument("--allowlist", type=Path, required=True)
    lock = python.add_mutually_exclusive_group(required=True)
    lock.add_argument("--uv-lock", type=Path)
    lock.add_argument(
        "--requirements", type=Path, help="uv pip compile output with --emit-index-annotation"
    )
    python.add_argument(
        "--pinned-out", type=Path, help="write the registry packages as name==version lines"
    )
    python.set_defaults(run=check_python)
    env = commands.add_parser("env", help="print KEY=VALUE lines for $GITHUB_ENV").add_subparsers(
        required=True
    )
    env_py = env.add_parser("python")
    env_py.add_argument("--allowlist", type=Path, required=True)
    env_py.set_defaults(run=env_python)
    return root


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        Error,
    ) as error:
        print(f"allowlist: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
