import re
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
        label = f"{package.name}@{package.version}"
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
