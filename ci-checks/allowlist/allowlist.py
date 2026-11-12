import argparse
import json
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
# Rust's entries sit at the top level, where cargo-deny reads them; each other language has its own table of the same keys.
LANGUAGES = ("rust", "python", "typescript")
SECTIONS = LANGUAGES[1:]
PEP_508_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")


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

    def __str__(self) -> str:
        return f"{self.name}@{self.version}" if self.version else self.name


COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def commit_of(url: str) -> str | None:
    """The commit a locked git URL records: its #fragment (uv.lock, npm, Cargo.lock) or trailing @rev (uv pip compile)."""
    rest, _, fragment = url.strip().partition("#")
    at = urllib.parse.urlsplit(rest.removeprefix("git+")).path.rpartition("@")[2]
    return next((rev.lower() for rev in (fragment, at) if COMMIT.fullmatch(rev.lower())), None)


class GitSource(NamedTuple):
    """A sources.allow-git entry: a repository URL, optionally pinned to one commit."""

    url: str
    rev: str | None = None

    def matches(self, url: str) -> bool:
        return normalize_git(url) == normalize_git(self.url) and self.rev in (None, commit_of(url))

    def __str__(self) -> str:
        return self.url if self.rev is None else f'{{ url = "{self.url}", rev = "{self.rev}" }}'


@dataclass(frozen=True)
class Allowlist:
    packages: tuple[Spec, ...] = ()
    build: tuple[Spec, ...] = ()
    git: tuple[GitSource, ...] = ()
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
    return spec(name, version)


def spec(name: str, version: str | None) -> Spec:
    return Spec(name, None if version in (None, "", "*") else version)


def to_spec(entry: Any, where: str) -> Spec:
    if isinstance(entry, str):
        return parse_spec(entry, where)
    if isinstance(entry, dict):
        if isinstance(crate := entry.get("crate"), str):
            return parse_spec(crate, where)
        name, version = entry.get("name"), entry.get("version")
        if isinstance(name, str) and name and (version is None or isinstance(version, str)):
            return spec(name, version)
    raise Error(f'{where}: {entry!r} needs a name: "name", "name@version" or {{ crate = ... }}')


def strings(entries: list[Any], where: str) -> tuple[str, ...]:
    if not all(isinstance(e, str) and e for e in entries):
        raise Error(f"{where}: entries must be non-empty strings")
    return tuple(entries)


def git_source(entry: Any, where: str) -> GitSource:
    if isinstance(entry, str) and entry:
        return GitSource(entry)
    if isinstance(entry, dict) and set(entry) <= {"url", "rev"}:
        url, rev = entry.get("url"), entry.get("rev")
        if isinstance(url, str) and url:
            if rev is None:
                return GitSource(url)
            if isinstance(rev, str) and COMMIT.fullmatch(rev.lower()):
                return GitSource(url, rev.lower())
    raise Error(f'{where}: {entry!r} must be "URL" or {{ url = "URL", rev = "<full commit SHA>" }}')


def license_exception(entry: Any, where: str) -> LicenseException:
    if not isinstance(entry, dict):
        raise Error(f"{where}: {entry!r} must be a table {{ crate = ..., allow = [...] }}")
    allow = entry.get("allow")
    if not isinstance(allow, list) or not allow:
        raise Error(f"{where}: {entry!r} needs a non-empty allow array")
    return LicenseException(to_spec(entry, where), strings(allow, where))


def load(path: Path, language: str) -> Allowlist:
    """The language's entries; every language's entries are validated. Absent file: no exceptions, the strictest rules."""
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return Allowlist()
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Error(f"{path}: {error}") from error
    tables = {"rust": {k: v for k, v in raw.items() if k not in SECTIONS}}
    for section in SECTIONS:
        table = raw.get(section, {})
        if not isinstance(table, dict):
            raise Error(f"{section}: must be a table of allowlist keys")
        tables[section] = table
    parsed = {
        lang: parse(table, "" if lang == "rust" else f"{lang}.") for lang, table in tables.items()
    }
    return parsed[language]


def parse(raw: dict[str, Any], prefix: str) -> Allowlist:
    found: dict[tuple[str, ...], list[Any]] = {}
    for key, value in leaves(raw):
        name = prefix + ".".join(key)
        if key not in KEYS:
            raise Error(
                f"{name}: not an allowlist key; allowed: {', '.join(sorted('.'.join(k) for k in KEYS))}"
                f", at the top level (Rust) or under [{'], ['.join(SECTIONS)}]"
            )
        if not isinstance(value, list):
            raise Error(f"{name}: must be an array")
        found[key] = value

    def entries(*key: str) -> list[Any]:
        return found.get(key, [])

    allowlist = Allowlist(
        packages=tuple(to_spec(e, prefix + "bans.allow") for e in entries("bans", "allow")),
        build=tuple(
            to_spec(e, prefix + "bans.build.allow-build-scripts")
            for e in entries("bans", "build", "allow-build-scripts")
        ),
        git=tuple(
            git_source(e, prefix + "sources.allow-git") for e in entries("sources", "allow-git")
        ),
        registries=strings(entries("sources", "allow-registry"), prefix + "sources.allow-registry"),
        licenses=tuple(
            license_exception(e, prefix + "licenses.exceptions")
            for e in entries("licenses", "exceptions")
        ),
        raw=raw,
    )
    if prefix == "python.":
        for built in allowlist.build:
            if not PEP_508_NAME.fullmatch(built.name):
                raise Error(
                    f"python.bans.build.allow-build-scripts: {built.name!r} is not a Python package name"
                )
    return allowlist


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


def git_violations(locked: Iterable[Locked], allowlist: Allowlist, prefix: str) -> list[str]:
    """The git rule alone. The suggested entry pins the locked commit when the repo is listed only pinned."""
    found: list[str] = []
    for package in locked:
        if package.kind != "git" or any(s.matches(package.url) for s in allowlist.git):
            continue
        repo = normalize_git(package.url)
        pinned = any(normalize_git(s.url) == repo for s in allowlist.git)
        commit = commit_of(package.url)
        entry = GitSource(f"https://{repo}", commit if pinned and commit else None)
        found.append(
            f"{str(package) or repo}: git source {package.url} is not allowed; "
            f"add {entry} to {prefix}sources.allow-git"
        )
    return found


def violations(
    locked: Iterable[Locked],
    allowlist: Allowlist,
    prefix: str,
    canonical: Callable[[str], str] = same,
) -> list[str]:
    """One line per package the rules reject, naming the allowlist entry (under prefix) that would admit it.

    Registries are not checked: python.yml and typescript.yml never limited them. A remote package comes from a
    URL no registry serves (a Python direct URL, an npm tarball URL); sources.allow-registry prefixes admit it.
    """
    found: list[str] = []
    for package in locked:
        if package.kind == "local":
            continue
        label = str(package)
        if package.kind == "remote" and not under(package.url, allowlist.registries):
            origin = urllib.parse.urlsplit(package.url)
            found.append(
                f"{label}: URL source {package.url} is not allowed; "
                f"add {origin.scheme}://{origin.netloc} to {prefix}sources.allow-registry"
            )
        if package.kind == "git":
            found += git_violations([package], allowlist, prefix)
        if allowlist.packages and not any(
            s.matches(package.name, package.version, canonical) for s in allowlist.packages
        ):
            found.append(f'{label}: not in {prefix}bans.allow; add "{label}"')
    return found


REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:==\s*([^\s;\\]+)|@\s*(\S+))"
)


UV_LOCAL_SOURCES = ("editable", "virtual", "path", "directory")
# uv pip compile writes these option lines; none of them names a package.
COMPILE_OPTIONS = (
    "--index-url",
    "--extra-index-url",
    "--find-links",
    "--no-index",
    "--no-binary",
    "--only-binary",
)


def read_uv_lock(path: Path) -> list[Locked]:
    locked: list[Locked] = []
    for package in tomllib.loads(path.read_text()).get("package", []):
        source: dict[str, str] = package.get("source", {})
        name, version = package["name"], package.get("version", "")
        if kind := next((k for k in ("registry", "git") if k in source), None):
            locked.append(Locked(name, version, kind, source[kind]))
        elif "url" in source:
            locked.append(Locked(name, version, "remote", source["url"]))
        elif any(k in source for k in UV_LOCAL_SOURCES):
            locked.append(Locked(name, version, "local", ""))
        else:
            raise Error(f"{path}: package {name} has an unknown source {source!r}")
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
            target = line.split(maxsplit=1)[1]
            if "://" in target:
                egg = re.search(r"[#&]egg=([A-Za-z0-9._-]+)", target)
                kind = url_kind(target, path)
                locked.append(Locked(egg.group(1) if egg else "", "", kind, target))
            else:
                locked.append(Locked(Path(target).name, "", "local", ""))
        elif line.startswith("-") and not raw[0].isspace():
            if re.split(r"[\s=]", line, maxsplit=1)[0] not in COMPILE_OPTIONS:
                raise Error(f"{path}: cannot read option line {line!r}")
        elif line and not raw[0].isspace():
            match = REQUIREMENT.match(line)
            if not match:
                raise Error(f"{path}: cannot read requirement {line!r}")
            name, version, url = match.groups()
            if version:
                locked.append(Locked(name, version, "registry", ""))
            else:
                kind = url_kind(url, path)
                locked.append(Locked(name, "", kind, "" if kind == "local" else url))
    return locked


def url_kind(url: str, where: Path) -> str:
    """The kind of a direct reference: git, a local file, or a remote URL (wheel or sdist)."""
    if url.startswith("git+"):
        return "git"
    if url.startswith("file:"):
        return "local"
    if url.startswith(("https:", "http:")):
        return "remote"
    raise Error(f"{where}: {url!r} is no known source")


def read_cargo_lock(path: Path) -> list[Locked]:
    locked: list[Locked] = []
    for package in tomllib.loads(path.read_text()).get("package", []):
        source: str = package.get("source", "")
        name, version = package["name"], package.get("version", "")
        if source.startswith("git+"):
            locked.append(Locked(name, version, "git", source))
        elif source.startswith(("registry+", "sparse+")):
            locked.append(Locked(name, version, "registry", source.partition("+")[2]))
        elif not source:
            locked.append(Locked(name, version, "local", ""))
        else:
            raise Error(f"{path}: package {name} has an unknown source {source!r}")
    return locked


DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "gist.github.com")
TARBALL = (".tgz", ".tar.gz", ".tar")


def git_spec(url: str) -> bool:
    """npm reads an https spec as git when it ends in .git or names a repo on a known git host."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    return path.endswith(".git") or (
        parts.netloc.lower() in GIT_HOSTS and not path.endswith(TARBALL)
    )


def registry_tarball(url: str) -> bool:
    """The tarball path registries serve: <name>/-/<file>.tgz, or GitHub Packages' /download/."""
    parts = urllib.parse.urlsplit(url)
    return "/-/" in parts.path or (
        parts.netloc == "npm.pkg.github.com" and parts.path.startswith("/download/")
    )


def read_package_lock(path: Path) -> list[Locked]:
    """Each installed package by where it resolved. Remote: a tarball that is no registry's, or that some
    package.json asks for by URL. package.json overrides do not reach the lock, so resolved URLs decide.
    No resolved: a registry package (omit-lockfile-registry-resolved, or bundled in its parent's tarball)."""
    packages = json.loads(path.read_text()).get("packages")
    if not isinstance(packages, dict):
        raise Error(f"{path}: no packages map; npm 7 or later writes it (lockfileVersion 2 or 3)")
    by_url = {
        name
        for entry in packages.values()
        for field_name in DEPENDENCY_FIELDS
        for name, wanted in entry.get(field_name, {}).items()
        if wanted.startswith(("https:", "http:")) and not git_spec(wanted)
    }
    locked: list[Locked] = []
    for key, entry in packages.items():
        if not key:
            continue
        name = entry.get("name") or key.rpartition("node_modules/")[2]
        resolved: str = entry.get("resolved", "")
        if entry.get("link") or "node_modules/" not in key or resolved.startswith("file:"):
            kind, resolved = "local", ""
        elif not resolved:
            kind = "registry"
        elif resolved.startswith(("git+", "git:", "github:")):
            kind = "git"
        elif not resolved.startswith(("https:", "http:")):
            raise Error(f"{path}: {key} resolved to {resolved!r}, which is no known source")
        elif registry_tarball(resolved) and key.rpartition("node_modules/")[2] not in by_url:
            kind = "registry"
        else:
            kind = "remote"
        locked.append(Locked(name, entry.get("version", ""), kind, resolved))
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


def read_python_lock(args: argparse.Namespace) -> list[Locked]:
    if args.uv_lock:
        return read_uv_lock(args.uv_lock)
    return read_requirements(args.requirements) if args.requirements else []


def check_python(args: argparse.Namespace) -> int:
    allowlist = load(args.allowlist, "python")
    return report(violations(read_python_lock(args), allowlist, "python.", pep503))


def check_rust(args: argparse.Namespace) -> int:
    """cargo-deny runs every other rule; its allow-git has no commit, so a pinned entry's commit is checked here."""
    allowlist = load(args.allowlist, "rust")
    return report(git_violations(read_cargo_lock(args.cargo_lock), allowlist, ""))


def check_typescript(args: argparse.Namespace) -> int:
    allowlist = load(args.allowlist, "typescript")
    return report(violations(read_package_lock(args.lock), allowlist, "typescript."))


def env_typescript(args: argparse.Namespace) -> int:
    """npm's allow-git and allow-remote are all or nothing; Dependency rules checks each URL before npm ci."""
    allowlist = load(args.allowlist, "typescript")
    if allowlist.git:
        print("npm_config_allow_git=all")
    if allowlist.registries:
        print("npm_config_allow_remote=all")
    if allowlist.build:
        print(f"ALLOW_BUILD={' '.join(dict.fromkeys(str(spec) for spec in allowlist.build))}")
    return 0


def licenses(args: argparse.Namespace) -> int:
    for exception in load(args.allowlist, args.language).licenses:
        print(f"{exception.spec}\t{';'.join(exception.allow)}")
    return 0


INCLUDE = re.compile(r"(?:-r|--requirement|-c|--constraint)(?:\s*=\s*|\s+|(?<=-[rc]))(\S+)")


def requirement_lines(path: Path, seen: set[Path]) -> Iterator[str]:
    """Each requirement as written, -r and -c files inline, continuations joined, comments dropped."""
    if path.resolve() in seen or not path.is_file():
        return
    seen.add(path.resolve())
    for raw in re.sub(r"\\\r?\n", " ", path.read_text()).splitlines():
        line = " ".join(re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].split())
        if include := INCLUDE.fullmatch(line):
            yield from requirement_lines(path.parent / include.group(1), seen)
        elif line:
            yield line


# A requirement whose source is a URL: [-e ][name[extras] @ ]URL[ ; markers]
DIRECT_REFERENCE = re.compile(
    r"(?:(?:-e|--editable)\s+)?(?:[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*@\s*)?"
    r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*://\S+)(?:\s+;.*)?"
)


def admits_source(url: str, allowlist: Allowlist) -> bool:
    """Whether an entry admits this git or remote source, and so its build: neither has a wheel to fall back on."""
    if url.startswith("git+"):
        return any(s.matches(url) for s in allowlist.git)
    return url.startswith(("https:", "http:")) and under(url, allowlist.registries)


def prebuild_python(args: argparse.Namespace) -> int:
    """The direct-reference requirements the allowlist admits, as written. uv caches metadata by the URL as
    written, so building each alone lets the strict resolve read a source that names no package. Only chooses
    what to build: admission is check python's, on uv's output, so a line missed here fails the resolve."""
    allowlist = load(args.allowlist, "python")
    seen: set[Path] = set()
    for path in args.files:
        for line in requirement_lines(path, seen):
            direct = DIRECT_REFERENCE.fullmatch(line)
            if direct and admits_source(direct.group("url"), allowlist):
                print(line)
    return 0


def env_python(args: argparse.Namespace) -> int:
    """UV_NO_BINARY_PACKAGE: the build entries, plus the git and remote sdist packages of a checked lock or
    resolution. An admitted git or remote source admits its build; a remote wheel needs none."""
    allowlist = load(args.allowlist, "python")
    names = [spec.name for spec in allowlist.build]
    names += [
        package.name
        for package in read_python_lock(args)
        if package.name
        and (
            package.kind == "git" or (package.kind == "remote" and not package.url.endswith(".whl"))
        )
    ]
    if names:
        print(f"UV_NO_BINARY_PACKAGE={' '.join(dict.fromkeys(names))}")
    return 0


def python_lock(command: argparse.ArgumentParser, required: bool) -> None:
    lock = command.add_mutually_exclusive_group(required=required)
    lock.add_argument("--uv-lock", type=Path)
    lock.add_argument(
        "--requirements", type=Path, help="uv pip compile output with --emit-index-annotation"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="allowlist.py", description="Apply the project dependency allowlist."
    )
    commands = root.add_subparsers(required=True)
    check = commands.add_parser(
        "check", help="check locked packages against the dependency rules"
    ).add_subparsers(required=True)
    rust = check.add_parser("rust")
    rust.add_argument("--allowlist", type=Path, required=True)
    rust.add_argument("--cargo-lock", type=Path, required=True)
    rust.set_defaults(run=check_rust)
    python = check.add_parser("python")
    python.add_argument("--allowlist", type=Path, required=True)
    python_lock(python, required=True)
    python.set_defaults(run=check_python)
    typescript = check.add_parser("typescript")
    typescript.add_argument("--allowlist", type=Path, required=True)
    typescript.add_argument("--lock", type=Path, required=True, help="package-lock.json")
    typescript.set_defaults(run=check_typescript)
    env = commands.add_parser("env", help="print KEY=VALUE lines for $GITHUB_ENV").add_subparsers(
        required=True
    )
    env_py = env.add_parser("python")
    env_py.add_argument("--allowlist", type=Path, required=True)
    python_lock(env_py, required=False)
    env_py.set_defaults(run=env_python)
    prebuild = commands.add_parser(
        "prebuild", help="print the git requirements to build before resolving"
    ).add_subparsers(required=True)
    prebuild_py = prebuild.add_parser("python")
    prebuild_py.add_argument("--allowlist", type=Path, required=True)
    prebuild_py.add_argument(
        "files", type=Path, nargs="+", help="requirement files; absent files are skipped"
    )
    prebuild_py.set_defaults(run=prebuild_python)
    env_ts = env.add_parser("typescript")
    env_ts.add_argument("--allowlist", type=Path, required=True)
    env_ts.set_defaults(run=env_typescript)
    license_lines = commands.add_parser(
        "licenses", help="print each licenses.exceptions entry as SPEC<TAB>LICENSE;..."
    )
    license_lines.add_argument("language", choices=SECTIONS)
    license_lines.add_argument("--allowlist", type=Path, required=True)
    license_lines.set_defaults(run=licenses)
    return root


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        Error,
    ) as error:
        print(f"allowlist: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
