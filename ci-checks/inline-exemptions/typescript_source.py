import re
from pathlib import PurePosixPath

import tree_sitter as ts
import tree_sitter_typescript

from syntax import Functions, Hit, Node, attached, generated, jscpd, leading_comments, parse, text, walk

TYPESCRIPT = ts.Language(tree_sitter_typescript.language_typescript())
TSX = ts.Language(tree_sitter_typescript.language_tsx())
TYPESCRIPT_SUFFIXES = (".ts", ".mts", ".cts")

TSC = re.compile(r"/?\s*@(?P<directive>ts-expect-error|ts-ignore|ts-nocheck)\b", re.I)
ESLINT_DISABLE = re.compile(r"eslint-disable(?:-next-line|-line)?(?=\s|$)(?P<rules>.*)", re.I | re.S)
ESLINT_CONFIG = re.compile(r"eslint\s(?P<config>.*)", re.I | re.S)
DESCRIPTION = re.compile(r"\s--\s.*|\s--$", re.S)
COVERAGE = re.compile(r"(?:v8|c8|istanbul)\s+ignore\s+(?P<keyword>next|start|if|else|file)\b", re.I)
PUBLIC = re.compile(r"(?:^|\s)@public\b")

ROOTS = frozenset({"test", "it", "describe", "suite"})
MARKERS = frozenset({"skip", "todo", "fails", "skipIf", "runIf"})
X_CALLS = frozenset({"xit", "xtest", "xdescribe"})
CONTEXTS = frozenset({"ctx", "context"})
FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
CLASSES = frozenset({"class_declaration", "abstract_class_declaration", "class"})


def body(comment: str) -> str:
    """The comment's text without its delimiters and the leading asterisks of JSDoc lines."""
    if comment.startswith("//"):
        return comment[2:].strip()
    lines = comment[2:-2].split("\n")
    return "\n".join(re.sub(r"^\s*\*+", "", line) for line in lines).strip()


def split_top(value: str, separator: str, limit: int = -1) -> list[str]:
    """Split on a separator outside brackets and quotes."""
    parts, depth, quote, start = [], 0, "", 0
    for index, char in enumerate(value):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'`":
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        elif char == separator and depth == 0 and len(parts) != limit:
            parts.append(value[start:index])
            start = index + 1
    return [*parts, value[start:]]


def unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`" else value


def disabled_rules(config: str) -> list[str]:
    """Rules an `/* eslint ... */` inline config turns off."""
    rules = []
    for item in split_top(config, ","):
        if len(pair := split_top(item, ":", 1)) != 2:
            continue
        severity = pair[1].strip()
        if severity.startswith("["):
            severity = split_top(severity[1:-1], ",")[0]
        if unquote(severity).lower() in ("off", "0"):
            rules.append(unquote(pair[0]))
    return rules


def comment_rules(comment: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(tool, rule) pairs of a comment: the first list belongs to the line or function, the second to the file."""
    local, file, content = [], [], body(comment)
    if match := TSC.match(content):
        directive = match.group("directive").lower()
        (file if directive == "ts-nocheck" else local).append(("tsc", directive))
    if match := ESLINT_DISABLE.match(content):
        rules = [r.strip() for r in DESCRIPTION.sub("", match.group("rules")).split(",") if r.strip()]
        local += [("eslint", rule) for rule in rules or ["*"]]
    elif comment.startswith("/*") and (match := ESLINT_CONFIG.match(content)):
        local += [("eslint", rule) for rule in disabled_rules(DESCRIPTION.sub("", match.group("config")))]
    if content.lower() == "prettier-ignore":
        local.append(("prettier", "ignore"))
    if match := COVERAGE.match(content):
        local.append(("vitest-coverage", match.group("keyword").lower()))
    if comment.startswith("/**") and PUBLIC.search(content):
        local.append(("knip", "public"))
    return local, file


def member_name(node: Node | None) -> str:
    return unquote(text(node)) if node is not None and node.type == "string" else text(node)


def class_name(node: Node) -> str:
    if name := node.child_by_field_name("name"):
        return text(name)
    if node.parent and node.parent.type == "variable_declarator":
        return text(node.parent.child_by_field_name("name"))
    return "default" if node.parent and node.parent.type == "export_statement" else ""


def object_owner(node: Node) -> str | None:
    """The variable an object literal member belongs to, if the object is a variable's value."""
    holder = node.parent
    if holder is None or holder.type != "object" or holder.parent is None or holder.parent.type != "variable_declarator":
        return None
    return text(holder.parent.child_by_field_name("name"))


def name_of(node: Node) -> str | None:
    """The FR-40 name of a function-like node, or None when the node does not name a function."""
    kind, value = node.type, node.child_by_field_name("value")
    if kind in ("function_declaration", "generator_function_declaration"):
        return text(node.child_by_field_name("name"))
    if kind == "method_definition" or (kind == "public_field_definition" and value and value.type in FUNCTION_VALUES):
        member = member_name(node.child_by_field_name("name"))
        if node.parent and node.parent.type == "class_body" and node.parent.parent and node.parent.parent.type in CLASSES:
            return f"{class_name(node.parent.parent)}.{member}"
        owner = object_owner(node) if kind == "method_definition" else None
        return f"{owner}.{member}" if owner else None
    if kind == "pair" and value and value.type in FUNCTION_VALUES and (owner := object_owner(node)):
        return f"{owner}.{member_name(node.child_by_field_name('key'))}"
    if kind == "variable_declarator" and value and value.type in FUNCTION_VALUES:
        name = node.child_by_field_name("name")
        return text(name) if name and name.type == "identifier" else None
    if kind in FUNCTION_VALUES and node.parent and node.parent.type == "export_statement":
        return "default" if node.child_by_field_name("name") is None else text(node.child_by_field_name("name"))
    return None


def functions(root: Node) -> Functions:
    return Functions([(node.start_byte, node.end_byte, name) for node in walk(root) if (name := name_of(node)) is not None])


def declared(node: Node) -> Node | None:
    if node.type == "export_statement":
        node = node.child_by_field_name("declaration") or node.child_by_field_name("value") or node
    if node.type in ("lexical_declaration", "variable_declaration") and node.named_child_count == 1:
        node = node.named_children[0]
    return node if name_of(node) is not None else None


def skipped(node: Node) -> bool:
    return node.type in ("comment", "decorator")


def vitest_roots(root: Node) -> frozenset[str]:
    names = set(ROOTS)
    for node in walk(root):
        source = node.child_by_field_name("source") if node.type == "import_statement" else None
        if source is None or unquote(text(source)) != "vitest":
            continue
        for specifier in (n for n in walk(node) if n.type == "import_specifier"):
            name, alias = specifier.child_by_field_name("name"), specifier.child_by_field_name("alias")
            if alias is not None and text(name) in ROOTS:
                names.add(text(alias))
    return frozenset(names)


def chain_root(node: Node | None) -> Node | None:
    while node is not None and node.type in ("member_expression", "call_expression"):
        node = node.child_by_field_name("object" if node.type == "member_expression" else "function")
    return node


def test_marker(node: Node, roots: frozenset[str]) -> str | None:
    if node.type == "member_expression":
        marker = text(node.child_by_field_name("property"))
        if marker in MARKERS:
            base = chain_root(node.child_by_field_name("object"))
            if base is not None and base.type == "identifier" and text(base) in roots:
                return marker
    elif node.type == "call_expression":
        function = node.child_by_field_name("function")
        if function is not None and function.type == "identifier" and text(function) in X_CALLS:
            return text(function)
        if function is not None and function.type == "member_expression":
            target, method = function.child_by_field_name("object"), text(function.child_by_field_name("property"))
            if target is not None and target.type == "identifier" and text(target) in CONTEXTS and method == "skip":
                return "skip"
    return None


def is_comment(node: Node) -> bool:
    return node.type == "comment"


def extract(path: str, source: bytes) -> list[Hit]:
    root = parse(TYPESCRIPT if PurePosixPath(path).suffix in TYPESCRIPT_SUFFIXES else TSX, source)
    found = functions(root)
    roots = vitest_roots(root)
    hits: list[tuple[int, Hit]] = [(0, hit) for hit in generated(leading_comments(root, is_comment, frozenset({"hash_bang_line"})))]
    for node in walk(root):
        if node.type == "comment":
            comment = text(node)
            target = attached(node, skipped, declared)
            function = found.at((target or node).start_byte)
            local, file = comment_rules(comment)
            hits += [(node.start_byte, (tool, rule, function)) for tool, rule in local]
            hits += [(node.start_byte, (tool, rule, "")) for tool, rule in file]
            hits += [(node.start_byte, hit) for hit in jscpd(comment, function)]
        elif marker := test_marker(node, roots):
            hits.append((node.start_byte, ("vitest", marker, found.at(node.start_byte))))
    return [hit for _, hit in sorted(hits, key=lambda item: item[0])]
