import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

HERE = Path(__file__).resolve().parent
CI_CHECKS = HERE.parent
sys.path.insert(0, str(CI_CHECKS / "config-overrides"))
from config_overrides import (
    DEFAULT_SOURCE_DIRS,
    OVERRIDES,
    Error as OverridesError,
    parse_overrides,
    project_sections,
    section_header,
    toml_string,
)

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
    scanner: str | None = None


LANGUAGES = {
    "python": Language((".py", ".pyi"), marker="__init__.py", recursive=True, scanner="python/layer-rules/imports.py"),
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


REGULAR = ("100644", "100755")
BYPASS, SKIP, UP, CYCLE = "绕开出口", "越层", "向上", "循环"

Pair = tuple[str, str]
Runner = Callable[..., Any]


class Error(Exception):
    pass


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args]
    try:
        result = subprocess.run(command, input=stdin, capture_output=True, check=False)
    except FileNotFoundError as error:
        raise Error("git not found") from error
    if result.returncode != 0:
        raise Error(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def tree(root: Path, rev: str, prefix: str) -> dict[str, str]:
    """Regular files under prefix at rev, relative to it, to their blob ids; symlinks and submodules are left out."""
    args = ["ls-tree", "-r", "-z", "--full-tree", rev]
    if prefix:
        args += ["--", prefix]
    files = {}
    for record in git(root, *args).split(b"\0"):
        if not record:
            continue
        meta, raw = record.split(b"\t", 1)
        mode, _, blob = meta.decode().split(" ")
        path = raw.decode("utf-8", "surrogateescape")
        if mode in REGULAR and path.startswith(prefix):
            files[path[len(prefix) :]] = blob
    return files


def blobs(root: Path, ids: set[str]) -> dict[str, bytes]:
    """Blob contents straight from the object store: no checkout, filter or working-tree-encoding applies."""
    order = sorted(ids)
    out = git(root, "cat-file", "--batch", stdin="".join(f"{i}\n" for i in order).encode())
    contents, at = {}, 0
    for blob in order:
        end = out.index(b"\n", at)
        size = int(out[at:end].split(b" ")[2])
        contents[blob] = out[end + 1 : end + 1 + size]
        at = end + 2 + size
    return contents


@dataclass
class Side:
    pairs: dict[Pair, list[int]] = field(default_factory=lambda: defaultdict(list))
    edges: dict[Pair, list[int]] = field(default_factory=lambda: defaultdict(list))
    unassigned: int = 0
    broken: list[tuple[str, str]] = field(default_factory=list)


def rule(source: int, target: int, to_exit: bool) -> str | None:
    if target < source:
        return UP
    if target == source + 1:
        return None if to_exit else BYPASS
    return SKIP if target > source + 1 else None


def scanner_records(language: str, side_root: Path, request: dict[str, Any], runner: Runner) -> list[dict[str, Any]]:
    scanner = CI_CHECKS / str(LANGUAGES[language].scanner)
    # -I -S: no site-packages, .pth, sitecustomize, PYTHON* variables or script-directory imports.
    argv = [sys.executable, "-I", "-S", str(scanner), str(side_root)]
    result = runner(argv, input=json.dumps(request), capture_output=True, text=True, cwd=scanner.parent, check=False)
    if result.returncode != 0:
        last = (result.stderr or "").strip().splitlines()[-1:] or [""]
        raise Error(f"{language} scanner exited with {result.returncode}: {last[0]}")
    records = []
    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise Error(f"{language} scanner wrote a line that is not JSON: {line[:200]}") from error
        records.append(record)
    return records


def scan_side(
    root: Path, rev: str, prefix: str, config: Config, language: str, runner: Runner
) -> tuple[Side, dict[str, str]]:
    files = {p: b for p, b in tree(root, rev, prefix).items() if language_of(p) == language}
    todo = sorted(p for p in files if config.layer_of(p) is not None and not config.is_test(p))
    side = Side()
    if not todo:
        return side, files
    contents = blobs(root, set(files.values()))
    with tempfile.TemporaryDirectory(prefix="layer-rules-") as tmp:
        for path, blob in files.items():
            target = Path(tmp) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[blob])
        request = {"files": sorted(files), "scan": todo, "source-dirs": list(config.source_dirs) or ["."]}
        records = scanner_records(language, Path(tmp), request, runner)
    scanned = set(todo)
    for record in records:
        if not isinstance(record, dict) or record.get("file") not in scanned:
            raise Error(f"{language} scanner wrote a malformed record: {str(record)[:200]}")
        path = record["file"]
        if set(record) == {"file", "error"} and isinstance(record["error"], str):
            side.broken.append((path, record["error"]))
            continue
        line, targets = record.get("line"), record.get("targets")
        if set(record) != {"file", "line", "targets"} or not isinstance(line, int) or not isinstance(targets, list):
            raise Error(f"{language} scanner wrote a malformed record: {str(record)[:200]}")
        if not all(isinstance(t, str) and t in files for t in targets):
            raise Error(f"{language} scanner named a file that is not in the project: {str(record)[:200]}")
        add(side, config, path, line, targets)
    return side, files


def add(side: Side, config: Config, path: str, line: int, targets: list[str]) -> None:
    source = config.layer_of(path)
    assert source is not None
    for target in dict.fromkeys(targets):
        if config.is_test(target) or target == path:
            continue
        layer = config.layer_of(target)
        if layer is None:
            side.unassigned += 1
        elif layer == source:
            side.edges[path, target].append(line)
        elif rule(source, layer, target == config.layers[layer].exit_file):
            side.pairs[path, target].append(line)


def components(edges: set[Pair]) -> dict[str, str]:
    """Each node's strongly connected component (Tarjan, iterative)."""
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(edges):
        graph[source].append(target)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    component: dict[str, str] = {}
    for start in sorted(graph):
        if start in index:
            continue
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        work = [(start, iter(graph[start]))]
        while work:
            node, successors = work[-1]
            successor = next(successors, None)
            if successor is None:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component[member] = node
                        if member == node:
                            break
            elif successor not in index:
                index[successor] = low[successor] = len(index)
                stack.append(successor)
                on_stack.add(successor)
                work.append((successor, iter(graph[successor])))
            elif successor in on_stack:
                low[node] = min(low[node], index[successor])
    return component


def path_between(edges: set[Pair], start: str, goal: str) -> list[str]:
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(edges):
        graph[source].append(target)
    previous: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            break
        for successor in graph[node]:
            if successor not in previous:
                previous[successor] = node
                queue.append(successor)
    chain: list[str] = []
    node: str | None = goal
    while node is not None:
        chain.append(node)
        node = previous[node]
    return chain[::-1]


def new_cycle_edges(head: Side, base: Side) -> list[tuple[Pair, list[str]]]:
    """Each head edge merge-base lacks that closes a cycle, with the cycle from its target round to its source."""
    edges = set(head.edges)
    component = components(edges)
    found = []
    for source, target in sorted(edges - set(base.edges)):
        if component.get(source) == component.get(target):
            found.append(((source, target), path_between(edges, target, source)))
    return found


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(path: str, line: int, message: str) -> str:
    return f"::error file={escape_property(path)},line={line},title=Layer rules::{escape_data(message)}"


def code(text: str) -> str:
    """A markdown code span safe inside a table cell: no newline, pipe or backtick run can end it."""
    text = toml_string(text)[1:-1].replace("|", "\\|")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    new: int = 0


def judge(config: Config, prefix: str, head: Side, base: Side) -> Report:
    report = Report()
    names = [layer.name for layer in config.layers]

    def shown(path: str) -> str:
        return prefix + path

    def layer_name(path: str) -> str:
        index = config.layer_of(path)
        assert index is not None
        return names[index]

    rows = []
    for pair in sorted(set(head.pairs) | set(base.pairs)):
        source, target = pair
        top, bottom = config.layer_of(source), config.layer_of(target)
        assert top is not None and bottom is not None
        exit_file = config.layers[bottom].exit_file
        name = rule(top, bottom, target == exit_file)
        before, now = len(base.pairs.get(pair, [])), len(head.pairs.get(pair, []))
        rows.append((name, source, names[top], target, names[bottom], before, now))
        if now <= before:
            continue
        report.new += 1
        message = f"{name}：{names[top]} → {names[bottom]}，{shown(source)} 引用 {shown(target)}"
        if name == BYPASS:
            message += f"，应走 {names[bottom]} 的出口 {shown(config.layers[bottom].exit)}"
        message += f"。merge-base {before} 处，head {now} 处，要减到不超过 {before} 处。"
        report.lines += [annotation(shown(source), line, message) for line in sorted(head.pairs[pair])]
    cycles = new_cycle_edges(head, base)
    for (source, target), chain in cycles:
        report.new += 1
        layer = layer_name(source)
        ring = " → ".join(shown(p) for p in [*chain, chain[0]])
        message = f"{CYCLE}：{layer} 层里 {ring}（{shown(source)} 引用 {shown(target)} 是 merge-base 没有的边）"
        report.lines += [annotation(shown(source), line, message) for line in sorted(head.edges[source, target])]
    report.summary = summary_lines(rows, len(cycles), head.unassigned)
    return report


def summary_lines(rows: list[tuple[Any, ...]], cycles: int, unassigned: int) -> list[str]:
    out = [
        f"新增的循环边：{cycles} 条。",
        f"未归层 import：{unassigned} 处（终点在项目里、不属于任何层）。",
        "",
    ]
    if not rows:
        return [*out, "没有违反层规则的引用。"]
    out += ["| 规则 | 发起文件 | 起层 | 目标文件 | 止层 | merge-base | head |", "|---|---|---|---|---|---|---|"]
    for name, source, top, target, bottom, before, now in rows:
        out.append(f"| {name} | {code(source)} | {code(top)} | {code(target)} | {code(bottom)} | {before} | {now} |")
    return out


def default_base(root: Path) -> str:
    try:
        return git(root, "rev-parse", "--abbrev-ref", "origin/HEAD").decode().strip()
    except Error as error:
        raise Error("找不到 origin 的默认分支（origin/HEAD），用 --base 指定") from error


def read_overrides(root: Path) -> dict[str, Any]:
    blob = tree(root, "HEAD", OVERRIDES).get("")
    if blob is None:
        return {}
    try:
        return parse_overrides(blobs(root, {blob})[blob].decode("utf-8"))
    except (UnicodeDecodeError, OverridesError) as error:
        raise ConfigError([f"{OVERRIDES}: {error}"]) from error


def check(
    root: Path,
    language: str,
    working_directory: str,
    base: str | None,
    out: Any,
    summary_path: str | None = None,
    runner: Runner = subprocess.run,
) -> int:
    def say(*lines: str) -> None:
        for line in lines:
            print(line, file=out)

    prefix = normalized(working_directory)
    prefix = f"{prefix}/" if prefix else ""
    try:
        if git(root, "status", "--porcelain", "-z", "--untracked-files=no"):
            say("Note: 工作区有未提交的改动；检查的是 HEAD commit，不含这些改动。")
        overrides = read_overrides(root)
        config = load_config(overrides, working_directory, language, frozenset(tree(root, "HEAD", prefix)))
        if config is None:
            keys = "、".join(toml_string(k) for k in layered_projects(overrides)) or "（无）"
            where = section_header(working_directory)
            say(f"Layer rules: {where} 没有 {language} 的 layers，不检查。repo-guards.toml 里设了 layers 的项目：{keys}")
            return 0
        fork = git(root, "merge-base", base or default_base(root), "HEAD").decode().strip()
        head, _ = scan_side(root, "HEAD", prefix, config, language, runner)
        if head.broken:
            say(*(f"error: head 上 {toml_string(prefix + p)} 解析不了：{toml_string(e)}" for p, e in head.broken))
            return 2
        before, _ = scan_side(root, fork, prefix, config, language, runner)
    except ConfigError as error:
        say(*(f"error: {problem}" for problem in error.problems))
        return 2
    except Error as error:
        say(f"error: {toml_string(str(error))}")
        return 2
    for path, message in before.broken:
        say(f"Layer rules: merge-base 上 {toml_string(prefix + path)} 解析不了，按没有 import 算：{toml_string(message)}")
    report = judge(config, prefix, head, before)
    title = f"### Layer rules（{language}，working-directory {code(working_directory)}，merge-base {fork[:12]}）"
    summary = [title, "", *report.summary]
    say(*report.lines, *summary)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8", errors="backslashreplace") as handle:
            handle.write("\n".join(summary) + "\n")
    if report.new:
        say(f"Layer rules: {report.new} 处新增违规（条数增加的文件对和闭合新环的边），见上面的注解。")
        return 1
    say("Layer rules: 没有新增违规。")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Layer rules: fail on layer violations and cycles new since the base.")
    parser.add_argument("language", choices=[name for name, spec in LANGUAGES.items() if spec.scanner])
    parser.add_argument("--working-directory", default=".", help="the project's directory, relative to the repo root")
    parser.add_argument("--base", help="compare with its merge-base with HEAD; default origin/HEAD")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    return check(Path.cwd(), args.language, args.working_directory, args.base, sys.stdout, summary)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
