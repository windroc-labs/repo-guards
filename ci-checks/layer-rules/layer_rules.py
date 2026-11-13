import functools
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

HERE = Path(__file__).resolve().parent
CI_CHECKS = HERE.parent
sys.path.insert(0, str(CI_CHECKS / "config-overrides"))
from config_overrides import DEFAULT_SOURCE_DIRS, Error as OverridesError, project_sections, section_header, toml_string

LAYER_KEYS = ("name", "paths", "exit")


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Language:
    suffixes: tuple[str, ...]
    # A directory exit is a package: Python's is marked by __init__.py and holds its subpackages, Go's is its own files.
    marker: str | None = None
    recursive: bool = False


LANGUAGES = {
    "python": Language((".py", ".pyi"), marker="__init__.py", recursive=True),
    "typescript": Language((".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")),
    "rust": Language((".rs",)),
    "go": Language((".go",)),
}


@dataclass(frozen=True)
class Layer:
    name: str
    paths: tuple[str, ...]
    exit: str
    exit_file: str
    language: str


@dataclass(frozen=True)
class Config:
    layers: tuple[Layer, ...]
    test_paths: tuple[str, ...]
    source_dirs: tuple[str, ...]

    def layer_of(self, path: str) -> int | None:
        return next((i for i, layer in enumerate(self.layers) if matches(layer.paths, path)), None)

    def is_test(self, path: str) -> bool:
        return matches(self.test_paths, path)


@functools.lru_cache(maxsize=None)
def glob_regex(pattern: str) -> re.Pattern[str]:
    parts = pattern.split("/")
    out = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]*/)*")
        else:
            segment = "".join("[^/]*" if char == "*" else re.escape(char) for char in part)
            out.append(segment if last else segment + "/")
    return re.compile("".join(out))


def matches(patterns: tuple[str, ...], path: str) -> bool:
    return any(glob_regex(p).fullmatch(path) for p in patterns)


def language_of(path: str) -> str | None:
    return next((name for name, spec in LANGUAGES.items() if path.endswith(spec.suffixes)), None)


def path_problem(path: str) -> str | None:
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts:
        return '不能为空、是绝对路径或带 ".."'
    return None


def normalized(directory: str) -> str:
    return "/".join(p for p in PurePosixPath(directory).parts if p != ".")


def under(directory: str, path: str, recursive: bool = True) -> bool:
    prefix = f"{directory}/" if directory else ""
    return path.startswith(prefix) and (recursive or "/" not in path[len(prefix) :])


def exit_target(exit_path: str, files: frozenset[str]) -> tuple[str, str] | str:
    """(language, the file an import of the exit resolves to), or what is wrong with the exit."""
    if exit_path in files:
        language = language_of(exit_path)
        return (language, exit_path) if language else "不是 Rust、Python、TypeScript 或 Go 的模块"
    directory = normalized(exit_path)
    inside = [f for f in files if under(directory, f)]
    if not inside:
        return "不存在"
    for language, spec in LANGUAGES.items():
        if spec.marker and f"{directory}/{spec.marker}" in files:
            return language, f"{directory}/{spec.marker}"
    if any(under(directory, f, recursive=False) and language_of(f) == "go" for f in inside):
        return "go", directory
    return "不是 Rust、Python、TypeScript 或 Go 的模块"


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def read_layers(raw: Any, where: str, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        problems.append(f"{where} layers 不是表的数组")
        return []
    layers = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            problems.append(f"{where} layers 第 {index} 项不是表")
            continue
        name = item.get("name")
        label = f"层 {toml_string(name)}" if isinstance(name, str) else f"layers 第 {index} 项"
        before = len(problems)
        problems += [f"{where} {label} 有未知键 {toml_string(k)}" for k in sorted(set(item) - set(LAYER_KEYS))]
        problems += [f"{where} {label} 缺少键 {toml_string(k)}" for k in LAYER_KEYS if k not in item]
        if "name" in item and (not isinstance(name, str) or not name):
            problems.append(f"{where} {label} 的 name 不是非空字符串")
        if "paths" in item and not string_list(item["paths"]):
            problems.append(f"{where} {label} 的 paths 不是字符串列表")
        if "exit" in item and not isinstance(item["exit"], str):
            problems.append(f"{where} {label} 的 exit 不是一个字符串")
        if len(problems) > before:
            continue
        for key, path in [*(("paths", p) for p in item["paths"]), ("exit", item["exit"])]:
            problem = path_problem(path)
            if problem:
                problems.append(f"{where} {label} 的 {key} {toml_string(path)}：{problem}")
        layers.append(item)
    seen: set[str] = set()
    for item in layers:
        if item["name"] in seen:
            problems.append(f"{where} 层名 {toml_string(item['name'])} 重复")
        seen.add(item["name"])
    return layers


def load_config(overrides: dict[str, Any], working_directory: str, language: str, files: frozenset[str]) -> Config | None:
    """This language's layers for the working-directory, or None when it has none; files is every file under it."""
    try:
        section = project_sections(overrides).get(working_directory, {})
    except OverridesError as error:
        raise ConfigError([str(error)]) from error
    if "layers" not in section:
        return None
    where = section_header(working_directory)
    problems: list[str] = []
    raw = read_layers(section["layers"], where, problems)
    test_paths = section.get("test-paths", [])
    if not string_list(test_paths):
        problems.append(f"{where} test-paths 不是字符串列表")
        test_paths = []
    for path in test_paths:
        problem = path_problem(path)
        if problem:
            problems.append(f"{where} test-paths {toml_string(path)}：{problem}")
    source_dirs = section.get("source-dirs", DEFAULT_SOURCE_DIRS)
    if not string_list(source_dirs):
        problems.append(f"{where} source-dirs 不是字符串列表")
        source_dirs = []
    if problems:
        raise ConfigError(problems)
    layers = []
    for item in raw:
        label = f"{where} 层 {toml_string(item['name'])} 的 exit {toml_string(item['exit'])}"
        found = exit_target(item["exit"], files)
        if isinstance(found, str):
            problems.append(f"{label}：{found}")
        else:
            layers.append(Layer(item["name"], tuple(item["paths"]), item["exit"], found[1], found[0]))
    if problems:
        raise ConfigError(problems)
    config = Config(
        tuple(layer for layer in layers if layer.language == language),
        tuple(test_paths),
        tuple(normalized(d) for d in source_dirs),
    )
    if not config.layers:
        return None
    problems += layer_problems(config, where, language, files)
    if problems:
        raise ConfigError(problems)
    return config


def layer_problems(config: Config, where: str, language: str, files: frozenset[str]) -> list[str]:
    spec = LANGUAGES[language]
    mine = sorted(f for f in files if language_of(f) == language)
    problems = []
    for layer in config.layers:
        label = f"{where} 层 {toml_string(layer.name)}"
        if layer.exit in files:
            outside = [] if matches(layer.paths, layer.exit) else [layer.exit]
        else:
            directory = normalized(layer.exit)
            outside = [f for f in mine if under(directory, f, spec.recursive) and not matches(layer.paths, f)]
        if outside:
            problems.append(f"{label} 的 exit {toml_string(layer.exit)}：不被本层 paths 匹配（{toml_string(outside[0])}）")
        if config.is_test(layer.exit_file) or config.is_test(layer.exit):
            problems.append(f"{label} 的 exit {toml_string(layer.exit)} 在 test-paths 里")
        if language == "python":
            for pattern in layer.paths:
                if not any(d == "" or pattern == d or pattern.startswith(f"{d}/") for d in config.source_dirs):
                    shown = "[" + ", ".join(toml_string(d or ".") for d in config.source_dirs) + "]"
                    problems.append(f"{label} 的 paths {toml_string(pattern)} 不在 source-dirs {shown} 里")
    for path in mine:
        names = [layer.name for layer in config.layers if matches(layer.paths, path)]
        if len(names) > 1:
            shown = "、".join(toml_string(n) for n in names)
            problems.append(f"{where} 文件 {toml_string(path)} 同时被层 {shown} 匹配")
    return problems


def layered_projects(overrides: dict[str, Any]) -> list[str]:
    try:
        return [key for key, section in project_sections(overrides).items() if "layers" in section]
    except OverridesError:
        return []
