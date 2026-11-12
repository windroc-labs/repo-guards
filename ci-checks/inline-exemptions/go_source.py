import re

import tree_sitter as ts
import tree_sitter_go

from syntax import Functions, Hit, Node, attached, generated, jscpd, leading_comments, parse, text, walk

LANGUAGE = ts.Language(tree_sitter_go.language())

# golangci-lint ignores `// nolint` (with a space) and needs a space, end or `:` right after the word.
NOLINT = re.compile(r"^//nolint(?::(?P<linters>\S+))?(?:\s|$)", re.I)
BUILD = re.compile(r"^//go:build\s+(?P<expr>.*\S)|^//\s*\+build\s+(?P<old>.*\S)")
SKIPS = {"Skip", "Skipf", "SkipNow"}
DECLARATIONS = {"function_declaration", "method_declaration"}


def receiver_type(node: Node) -> str:
    """The receiver's type name without `*`, package qualifier or type arguments."""
    receiver = node.child_by_field_name("receiver")
    for parameter in receiver.named_children if receiver else []:
        for part in walk(parameter.child_by_field_name("type") or parameter):
            if part.type == "type_identifier":
                return text(part)
    return ""


def functions(root: Node) -> Functions:
    spans = []
    for node in walk(root):
        if node.type in DECLARATIONS:
            name = text(node.child_by_field_name("name"))
            if node.type == "method_declaration":
                name = f"{receiver_type(node)}.{name}"
            spans.append((node.start_byte, node.end_byte, name))
    return Functions(spans)


def is_comment(node: Node) -> bool:
    return node.type == "comment"


def declared(node: Node) -> Node | None:
    return node if node.type in DECLARATIONS else None


def linters(match: re.Match[str]) -> list[str]:
    names = [name for name in (match.group("linters") or "").split(",") if name]
    return ["*" if name.lower() == "all" else name for name in names] or ["*"]


def comment_hits(node: Node, function: str, package: int) -> list[Hit]:
    comment = text(node).strip()
    hits: list[Hit] = []
    if match := NOLINT.match(comment):
        owner = "" if node.end_byte <= package else function
        hits += [("golangci-lint", linter, owner) for linter in linters(match)]
    if match := BUILD.match(comment):
        hits.append(("go-build", match.group("expr") or match.group("old"), ""))
    return hits + jscpd(comment, function)


def extract(path: str, source: bytes) -> list[Hit]:
    root = parse(LANGUAGE, source)
    found = functions(root)
    package = next((child.start_byte for child in root.children if child.type == "package_clause"), 0)
    hits: list[tuple[int, Hit]] = [(0, hit) for hit in generated(leading_comments(root, is_comment))]
    for node in walk(root):
        if node.type == "comment":
            target = attached(node, is_comment, declared)
            function = found.at((target or node).start_byte)
            hits += [(node.start_byte, hit) for hit in comment_hits(node, function, package)]
        elif node.type == "call_expression":
            callee = node.child_by_field_name("function")
            if callee is not None and callee.type == "selector_expression":
                field = text(callee.child_by_field_name("field"))
                if field in SKIPS:
                    hits.append((node.start_byte, ("go-test", field, found.at(node.start_byte))))
    return [hit for _, hit in sorted(hits, key=lambda item: item[0])]
