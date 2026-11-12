import re
from typing import NamedTuple

import tree_sitter as ts
import tree_sitter_rust

from syntax import Functions, Hit, Node, attached, generated, jscpd, leading_comments, parse, text, walk

LANGUAGE = ts.Language(tree_sitter_rust.language())

FUNCTIONS = {"function_item", "function_signature_item"}
COMMENTS = {"line_comment", "block_comment"}
COVERAGE_OFF = ("cargo-llvm-cov", "off")
TOKEN = re.compile(
    r"""(?P<string>b?r(?P<hashes>\#*)"(?P<raw>.*?)"(?P=hashes)|b?"(?P<plain>(?:\\.|[^"\\])*)")"""
    r"|(?P<path>(?:r\#)?[$\w]+(?:\s*::\s*(?:r\#)?[$\w]+)*)"
    r"|(?P<punct>\S)",
    re.S,
)
OPEN, CLOSE = "([{", ")]}"

Token = tuple[str, str]


class Meta(NamedTuple):
    """One item of an attribute: `path`, `path(args, ...)` or `path = value`."""

    path: str
    args: list["Meta"] | None
    value: str | None


def tokens(attribute: str) -> list[Token]:
    found = []
    for match in TOKEN.finditer(attribute):
        if match.group("string") is not None:
            found.append(("string", match.group("raw") if match.group("raw") is not None else match.group("plain")))
        elif match.group("path") is not None:
            found.append(("path", re.sub(r"\s+", "", match.group("path"))))
        else:
            found.append(("punct", match.group("punct")))
    return found


def is_punct(token: Token, chars: str) -> bool:
    return token[0] == "punct" and token[1] in chars


def skip_group(items: list[Token], pos: int) -> int:
    depth = 0
    while pos < len(items):
        depth += is_punct(items[pos], OPEN) - is_punct(items[pos], CLOSE)
        pos += 1
        if depth <= 0:
            break
    return pos


def meta_list(items: list[Token], pos: int = 0) -> tuple[list[Meta], int]:
    """Comma-separated meta items up to the closing bracket of this level; returns them and that bracket's position."""
    metas = []
    while pos < len(items) and not is_punct(items[pos], CLOSE):
        path, args, value = "", None, None
        if items[pos][0] == "path":
            path, pos = items[pos][1], pos + 1
        if pos < len(items) and items[pos] == ("punct", "("):
            args, pos = meta_list(items, pos + 1)
            pos += 1
        elif pos < len(items) and items[pos] == ("punct", "=") and pos + 1 < len(items):
            value, pos = items[pos + 1][1], pos + 2
        while pos < len(items) and not is_punct(items[pos], "," + CLOSE):
            pos = skip_group(items, pos) if is_punct(items[pos], OPEN) else pos + 1
        metas.append(Meta(path, args, value))
        if pos < len(items) and items[pos] == ("punct", ","):
            pos += 1
    return metas, pos


def names(args: list[Meta] | None) -> list[str]:
    return [arg.path for arg in args or [] if arg.path and arg.args is None and arg.value is None]


def meta_rules(meta: Meta) -> list[tuple[str, str]]:
    path, args = meta.path, meta.args
    if path in ("allow", "expect"):
        return [("clippy" if lint.startswith("clippy::") else "rustc", lint) for lint in names(args) or ["*"]]
    if path == "rustfmt::skip" or path.startswith("rustfmt::skip::"):
        return [("rustfmt", "skip")]
    if path == "coverage" and names(args) == ["off"]:
        return [COVERAGE_OFF]
    if path == "ignore":
        return [("cargo-test", "ignore")]
    if path == "no_sanitize":
        return [("sanitizer", name) for name in names(args)]
    if path == "sanitize":
        return [("sanitizer", arg.path) for arg in args or [] if arg.path and arg.value == "off"]
    if path == "cfg_attr" and args:
        return [rule for arg in args[1:] for rule in meta_rules(arg)]
    return []


def attribute_rules(attribute: str) -> list[tuple[str, str]]:
    return [rule for meta in meta_list(tokens(attribute))[0] for rule in meta_rules(meta)]


def type_name(node: Node | None) -> str:
    while node is not None and node.type in ("generic_type", "reference_type", "scoped_type_identifier"):
        node = node.child_by_field_name("name" if node.type == "scoped_type_identifier" else "type")
    return re.sub(r"\s+", "", text(node))


def function_name(node: Node) -> str:
    name = text(node.child_by_field_name("name"))
    body = node.parent
    owner = body.parent if body is not None and body.type == "declaration_list" else None
    if owner is not None and owner.type == "impl_item":
        return f"{type_name(owner.child_by_field_name('type'))}::{name}"
    if owner is not None and owner.type == "trait_item":
        return f"{text(owner.child_by_field_name('name'))}::{name}"
    return name


def functions(root: Node) -> Functions:
    return Functions([(n.start_byte, n.end_byte, function_name(n)) for n in walk(root) if n.type in FUNCTIONS])


def is_comment(node: Node) -> bool:
    """A comment that is not a doc comment: doc comments are documentation, not comments (FR-31)."""
    return (
        node.type in COMMENTS
        and node.child_by_field_name("outer") is None
        and node.child_by_field_name("inner") is None
    )


def is_extra(node: Node) -> bool:
    return node.type in COMMENTS or node.type == "attribute_item"


def declared(node: Node) -> Node | None:
    return node if node.type in FUNCTIONS else None


def item_after(attribute: Node) -> Node | None:
    following = attribute.next_named_sibling
    while following is not None and is_extra(following):
        following = following.next_named_sibling
    return following


def attribute_text(node: Node) -> str:
    return text(next((child for child in node.named_children if child.type == "attribute"), None))


def test_module(item: Node | None) -> bool:
    """A `mod` with an outer attribute that is exactly `cfg(test)` (FR-12)."""
    if item is None or item.type != "mod_item":
        return False
    previous = item.prev_named_sibling
    while previous is not None and is_extra(previous):
        if previous.type == "attribute_item" and re.sub(r"\s+", "", attribute_text(previous)) == "cfg(test)":
            return True
        previous = previous.prev_named_sibling
    return False


def macro_attributes(definition: Node) -> list[tuple[int, str]]:
    """`#[...]` and `#![...]` written as tokens in a `macro_rules!` body: (byte, attribute text)."""
    found = []
    for node in walk(definition):
        if node.type != "#" or node.parent is None or node.parent.type != "token_tree":
            continue
        following = node.next_sibling
        if following is not None and following.type == "!":
            following = following.next_sibling
        if following is not None and following.type == "token_tree" and following.children[0].type == "[":
            found.append((node.start_byte, text(following)[1:-1]))
    return found


def counted(attribute: Node, in_test_module: bool) -> list[tuple[str, str]]:
    rules = attribute_rules(attribute_text(attribute))
    return [rule for rule in rules if not (in_test_module and rule == COVERAGE_OFF)]


def extract(path: str, source: bytes) -> list[Hit]:
    root = parse(LANGUAGE, source)
    found = functions(root)
    header = leading_comments(root, is_comment, frozenset({"shebang"}))
    hits: list[tuple[int, Hit]] = [(0, hit) for hit in generated(header)]
    for node in walk(root):
        if node.type == "attribute_item":
            item = item_after(node)
            function = found.at((item if item is not None and item.type in FUNCTIONS else node).start_byte)
            hits += [(node.start_byte, (tool, rule, function)) for tool, rule in counted(node, test_module(item))]
        elif node.type == "inner_attribute_item":
            body = node.parent
            in_test_module = body is not None and body.type == "declaration_list" and test_module(body.parent)
            function = found.at(node.start_byte)
            hits += [(node.start_byte, (tool, rule, function)) for tool, rule in counted(node, in_test_module)]
        elif node.type == "macro_definition":
            for byte, attribute in macro_attributes(node):
                hits += [(byte, (tool, rule, found.at(byte))) for tool, rule in attribute_rules(attribute)]
        elif is_comment(node):
            target = attached(node, is_extra, declared)
            hits += [(node.start_byte, hit) for hit in jscpd(text(node), found.at((target or node).start_byte))]
    return [hit for _, hit in sorted(hits, key=lambda item: item[0])]
