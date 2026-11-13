"""Layer rules scanner for Python: lists each import statement and the project files it resolves to.

Protocol for every language's scanner (ci-checks/layer-rules/layer_rules.py runs it once per side): argv ends with
the side's root directory, which holds the "scan" and "context" files. stdin is one JSON object, paths relative to
the root: "files" names every source file of the language (not all are written), "scan" the ones to parse, "context"
the language's resolution files (go.mod, tsconfig.json, Cargo.toml; none for Python), "settings" the project
settings ("source-dirs"). stdout has one JSON line per statement, {"file", "line", "targets"}, targets being the
"files" it resolves to (a package-unit language may name the package directory), or {"file", "error"} for a file
that does not parse. References outside the project are left out.
"""

import ast
import json
import sys
from pathlib import Path, PurePosixPath

USAGE = "usage: imports.py <side root>  (JSON request on stdin)"


class Error(Exception):
    pass


def bases_of(source_dirs):
    out = []
    for directory in source_dirs:
        parts = [p for p in PurePosixPath(directory).parts if p != "."]
        out.append("/".join(parts))
    return out


def module_file(parts, root, files):
    base = "/".join(p for p in (root, *parts) if p)
    package = f"{base}/" if base else ""
    candidates = (f"{package}__init__.py", f"{package}__init__.pyi")
    if parts:
        candidates = (f"{base}.py", candidates[0], f"{base}.pyi", candidates[1])
    return next((c for c in candidates if c in files), None)


def resolve(parts, bases, files, exact=False):
    """The file of the longest importable prefix of parts (only all of parts when exact)."""
    for end in range(len(parts), 0 if parts else -1, -1):
        for root in bases:
            found = module_file(parts[:end], root, files)
            if found:
                return found
        if exact:
            return None
    return None


def package_dir(path, level):
    parts = list(PurePosixPath(path).parent.parts)
    if level - 1 > len(parts):
        return None
    return "/".join(parts[: len(parts) - (level - 1)])


def targets_of(node, path, bases, files):
    if isinstance(node, ast.Import):
        return {resolve(alias.name.split("."), bases, files) for alias in node.names}
    if node.level:
        directory = package_dir(path, node.level)
        if directory is None:
            return set()
        bases = [directory]
    parts = node.module.split(".") if node.module else []
    found = set()
    module = None
    for alias in node.names:
        sub = None if alias.name == "*" else resolve([*parts, alias.name], bases, files, exact=True)
        if sub:
            found.add(sub)
        else:
            module = module or resolve(parts, bases, files)
            found.add(module)
    return found


def scan(path, data, bases, files):
    try:
        tree = ast.parse(data, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        message = f"{type(error).__name__}: {error}".splitlines()[0][:300]
        return [{"file": path, "error": message}]
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    records = []
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
        targets = targets_of(node, path, bases, files) - {None, path}
        if targets:
            records.append({"file": path, "line": node.lineno, "targets": sorted(targets)})
    return records


def relative(path):
    pure = PurePosixPath(path)
    if not isinstance(path, str) or not path or pure.is_absolute() or ".." in pure.parts:
        raise Error(f"bad path {json.dumps(path)}")
    return path


def main(argv):
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    root = Path(argv[0])
    try:
        request = json.loads(sys.stdin.read())
        files = {relative(p) for p in request["files"]}
        todo = [relative(p) for p in request["scan"]]
        bases = bases_of(request["settings"]["source-dirs"])
        for path in todo:
            for record in scan(path, (root / path).read_bytes(), bases, files):
                print(json.dumps(record))
    except (OSError, ValueError, KeyError, TypeError, Error) as error:
        print(f"imports.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
