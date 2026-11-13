import ast
import json
import keyword
import logging
import subprocess
import sys
import tempfile
import tomllib
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

PREFIX = "api snapshot (python): "

try:
    import griffe
except ImportError as error:
    # A broken tool install means the check could not run: exit 2, never the mismatch code 1.
    print(f"{PREFIX}cannot import griffe: {error}", file=sys.stderr)
    sys.exit(2)
SOURCE_SUFFIXES = (".py", ".pyi")


class Entry(NamedTuple):
    # The compare core's field order: the first four fields are the key, so tuple order is key order.
    unit: str
    parent: str
    kind: str
    name: str
    signature: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self[:4]


class Error(Exception):
    pass


def quote(text: str) -> str:
    # Same as the compare core: no raw line breaks, and "#" escaped against the legacy "##[command]" syntax.
    return json.dumps(text).replace("#", "\\u0023")


def is_workspace(project: Path) -> bool:
    path = project / "pyproject.toml"
    if not path.is_file():
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Error(f"cannot read pyproject.toml: {quote(str(error))}") from error
    return "workspace" in data.get("tool", {}).get("uv", {})


def build(project: Path, out: Path) -> list[Path]:
    if not (project / "pyproject.toml").is_file() and not (project / "setup.py").is_file():
        raise Error("the project has no pyproject.toml or setup.py: cannot build a wheel")
    command = ["uv", "build", "--wheel", "--out-dir", str(out)]
    if is_workspace(project):
        command.append("--all-packages")
    try:
        code = subprocess.run(command, cwd=project, check=False).returncode
    except OSError as error:
        raise Error(f"cannot run uv build: {type(error).__name__}") from error
    if code != 0:
        raise Error(f"uv build failed with exit code {code}")
    wheels = sorted(out.glob("*.whl"))
    if not wheels:
        raise Error("uv build produced no wheel")
    return wheels


def extract(wheels: list[Path], site: Path) -> None:
    # Wheels come from the PR's build; extractall drops absolute and ".." member names and creates no symlinks.
    for wheel in wheels:
        try:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(site)
        except (zipfile.BadZipFile, OSError) as error:
            raise Error(f"cannot extract {quote(wheel.name)}: {type(error).__name__}") from error


def importable(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def units(site: Path) -> list[str]:
    names = set()
    for path in site.iterdir():
        name = path.name if path.is_dir() else path.stem if path.suffix in SOURCE_SUFFIXES else ""
        if importable(name) and not name.startswith("_"):
            names.add(name)
    if not names:
        raise Error("the wheel(s) hold no public package or module")
    return sorted(names)


def is_package(directory: Path) -> bool:
    return any((directory / f"__init__{suffix}").is_file() for suffix in SOURCE_SUFFIXES)


def module_files(site: Path, unit: str) -> list[Path]:
    """The unit's source files griffe loads: module names only, in package directories below the unit's own."""
    files = [site / f"{unit}{suffix}" for suffix in SOURCE_SUFFIXES if (site / f"{unit}{suffix}").is_file()]
    root = site / unit
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            directories = path.relative_to(root).parents[:-1]
            if (
                path.suffix in SOURCE_SUFFIXES
                and path.is_file()
                and importable(path.stem)
                and all(importable(part.name) and is_package(root / part) for part in directories)
            ):
                files.append(path)
    return files


def check_sources(site: Path, names: list[str]) -> None:
    # Griffe drops a submodule it cannot parse with only a debug log; a missing module must stop the check instead.
    for unit in names:
        for path in module_files(site, unit):
            relative = path.relative_to(site).as_posix()
            try:
                ast.parse(path.read_bytes(), filename=relative)
            except SyntaxError as error:
                reason = f"{quote(str(error.msg))} (line {error.lineno})"
                raise Error(f"{quote(relative)}: cannot parse: {reason}") from error
            except (ValueError, OSError) as error:
                raise Error(f"{quote(relative)}: cannot parse: {type(error).__name__}") from error


def text(value: Any) -> str:
    return "" if value is None else str(value)


def labels(obj: Any) -> str:
    return "".join(f"{label} " for label in sorted(obj.labels))


def type_parameters(obj: Any) -> str:
    parts = []
    for parameter in obj.type_parameters:
        stars = {"type-var-tuple": "*", "param-spec": "**"}.get(parameter.kind.value, "")
        part = stars + parameter.name
        if parameter.annotation is not None:
            part += f": {text(parameter.annotation)}"
        if parameter.default is not None:
            part += f" = {text(parameter.default)}"
        parts.append(part)
    return f"[{', '.join(parts)}]" if parts else ""


def parameters(function: Any) -> str:
    parts: list[str] = []
    kinds = [parameter.kind for parameter in function.parameters]
    for index, parameter in enumerate(function.parameters):
        kind = parameter.kind
        previous = kinds[index - 1] if index else None
        if kind is griffe.ParameterKind.keyword_only and previous not in (kind, griffe.ParameterKind.var_positional):
            parts.append("*")
        if kind is griffe.ParameterKind.var_positional:
            part = f"*{parameter.name}"
        elif kind is griffe.ParameterKind.var_keyword:
            part = f"**{parameter.name}"
        else:
            part = parameter.name
        if parameter.annotation is not None:
            part += f": {text(parameter.annotation)}"
        variadic = kind in (griffe.ParameterKind.var_positional, griffe.ParameterKind.var_keyword)
        if parameter.default is not None and not variadic:
            part += f" = {text(parameter.default)}"
        parts.append(part)
        following = kinds[index + 1] if index + 1 < len(kinds) else None
        if kind is griffe.ParameterKind.positional_only and following is not griffe.ParameterKind.positional_only:
            parts.append("/")
    return ", ".join(parts)


def one_function(function: Any) -> str:
    returns = f" -> {text(function.returns)}" if function.returns is not None else ""
    return f"{labels(function)}{type_parameters(function)}({parameters(function)}){returns}"


def function_signature(function: Any) -> str:
    if function.overloads:
        return "\n".join(one_function(overload) for overload in function.overloads)
    return one_function(function)


def class_signature(cls: Any) -> str:
    bases = f"({', '.join(text(base) for base in cls.bases)})" if cls.bases else ""
    return f"{labels(cls)}class{type_parameters(cls)}{bases}"


def attribute_signature(attribute: Any) -> str:
    signature = " ".join(sorted(attribute.labels))
    if attribute.annotation is not None:
        signature += f": {text(attribute.annotation)}"
    # A value assigned only inside a method is implementation, not interface.
    if attribute.value is not None and attribute.labels & {"module-attribute", "class-attribute"}:
        signature += f" = {text(attribute.value)}"
    return signature


def type_alias_signature(alias: Any) -> str:
    return f"type{type_parameters(alias)} = {text(alias.value)}"


FORMATTERS: dict[griffe.Kind, Callable[[Any], str]] = {
    griffe.Kind.MODULE: lambda module: "",
    griffe.Kind.CLASS: class_signature,
    griffe.Kind.FUNCTION: function_signature,
    griffe.Kind.ATTRIBUTE: attribute_signature,
    griffe.Kind.TYPE_ALIAS: type_alias_signature,
}


def is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def promote_overloads(obj: Any) -> None:
    """Make each @overload-only function (as in .pyi stubs) a member, as griffe does once an implementation follows.

    Griffe keeps such declarations apart from the members, so without this they would not be inherited,
    resolve as export targets, or override a base class method.
    """
    for name, overloads in list(obj.overloads.items()):
        if overloads and name not in obj.members:
            overloads[0].overloads = overloads
            obj.set_member(name, overloads[0])
            del obj.overloads[name]
    for member in obj.members.values():
        if not member.is_alias and (member.is_module or member.is_class):
            promote_overloads(member)


def walk(obj: Any, path: str, unit: str, stack: tuple[str, ...], entries: list[Entry]) -> None:
    members = obj.all_members if obj.is_class else obj.members
    for name, member in sorted(members.items()):
        if not member.is_public:
            continue
        try:
            target = member.final_target if member.is_alias else member
        except (griffe.AliasResolutionError, griffe.CyclicAliasError):
            entries.append(Entry(unit, path, "alias", name, member.target_path))
            continue
        if target.is_attribute and is_dunder(name):
            continue
        if target.is_module and member.is_alias:
            entries.append(Entry(unit, path, "module", name, target.path))
            continue
        entries.append(Entry(unit, path, target.kind.value, name, FORMATTERS[target.kind](target)))
        if (target.is_class and target.path not in stack) or target.is_module:
            walk(target, f"{path}.{name}", unit, (*stack, target.path), entries)


def generate(site: Path, names: list[str]) -> list[Entry]:
    loader = griffe.GriffeLoader(search_paths=[site], allow_inspection=False)
    modules = []
    for unit in names:
        try:
            modules.append(loader.load(unit, try_relative_path=False))
        except (griffe.GriffeError, ImportError) as error:
            raise Error(f"cannot load {quote(unit)}: {type(error).__name__}: {quote(str(error))}") from error
    for module in modules:
        promote_overloads(module)
    entries: list[Entry] = []
    for unit, module in zip(names, modules, strict=True):
        entries.append(Entry(unit, "", "module", unit, ""))
        walk(module, unit, unit, (), entries)
    return sorted(entries)


def write(path: str, content: str) -> None:
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise Error(f"cannot write {quote(path)}: text is not valid UTF-8") from error
    try:
        Path(path).write_bytes(data)
    except OSError as error:
        raise Error(f"cannot write {quote(path)}: {type(error).__name__}") from error


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python_api.py PROJECT_DIR OUTPUT", file=sys.stderr)
        return 2
    project, output = Path(argv[0]), argv[1]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wheels = build(project, Path(tmp) / "wheels")
            site = Path(tmp) / "site"
            extract(wheels, site)
            names = units(site)
            check_sources(site, names)
            entries = generate(site, names)
        write(output, json.dumps([entry._asdict() for entry in entries], indent=2, ensure_ascii=False) + "\n")
    except Error as error:
        print(f"{PREFIX}{error}", file=sys.stderr)
        return 2
    print(f"{PREFIX}{len(entries)} entries from {len(wheels)} wheel(s), units: {', '.join(map(quote, names))}")
    return 0


def cli(argv: list[str]) -> int:
    # Griffe's log lines and Python's SyntaxWarnings would carry PR-controlled text; what matters surfaces as an Error.
    logging.getLogger("griffe").disabled = True
    warnings.simplefilter("ignore")
    try:
        return main(argv)
    except Exception as error:  # noqa: BLE001 -- anything unexpected means the check could not run.
        print(f"{PREFIX}{type(error).__name__}: {quote(str(error))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
