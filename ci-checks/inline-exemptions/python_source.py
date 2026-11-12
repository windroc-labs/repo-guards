import re
from pathlib import PurePosixPath

import tree_sitter as ts
import tree_sitter_python

from syntax import Functions, Hit, Node, attached, generated, jscpd, leading_comments, parse, text, walk

LANGUAGE = ts.Language(tree_sitter_python.language())

CODES = r"[A-Z]+[0-9]+(?:[,\s]+[A-Z]+[0-9]+)*"
NOQA = re.compile(rf"#\s*noqa(?::\s*(?P<codes>{CODES}))?", re.I)
FILE_NOQA = re.compile(rf"#\s*(?:ruff|flake8)\s*:\s*noqa(?:\s*:\s*(?P<codes>{CODES}))?", re.I)
FORMAT = re.compile(r"#\s*(?:fmt\s*:\s*(?P<fmt>off|skip)|yapf\s*:\s*disable)\b", re.I)
IGNORE = re.compile(r"#\s*(?:type|pyright)\s*:\s*ignore\b(?:\[(?P<rules>[^\]]*)\])?", re.I)
PYRIGHT = re.compile(r"#\s*pyright\s*:\s*(?!ignore\b)(?P<settings>\S.*)", re.I)
# coverage.py's default exclusion patterns, case-insensitive.
PRAGMA = re.compile(r"#\s*pragma[:\s]?\s*no\s*(?P<what>cover|branch)", re.I)

MARKERS = {
    **{f"pytest.mark.{name}": ("pytest", name) for name in ("skip", "skipif", "xfail")},
    **{f"unittest.{name}": ("unittest", name) for name in ("skip", "skipIf", "skipUnless", "expectedFailure")},
    "typing.no_type_check": ("pyright", "no_type_check"),
}
CALLS = {f"pytest.{name}": name for name in ("skip", "xfail", "importorskip")}
SKIP_TEST = {"unittest.SkipTest", "unittest.case.SkipTest"}
MODULES = ("pytest", "unittest", "typing")
# An identifier in one of these (parent type, field) places names something rather than refers to it.
NOT_REFERENCES = {
    ("attribute", "attribute"), ("keyword_argument", "name"), ("function_definition", "name"),
    ("class_definition", "name"), ("parameters", None), ("default_parameter", "name"), ("typed_parameter", None),
}


def codes(match: re.Match[str]) -> list[str]:
    return re.split(r"[,\s]+", match.group("codes")) if match.group("codes") else ["*"]


def ruff_or_vulture(rule: str) -> str:
    return "vulture" if rule[:1] in "Vv" and rule != "*" else "ruff"


def comment_rules(comment: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(tool, rule) pairs of a comment: the first list belongs to the line or function, the second to the file."""
    local, file = [], []
    if match := FILE_NOQA.search(comment):
        file += [(ruff_or_vulture(code), code) for code in codes(match)]
    elif match := NOQA.search(comment):
        local += [(ruff_or_vulture(code), code) for code in codes(match)]
    if match := FORMAT.search(comment):
        local.append(("ruff-format", (match.group("fmt") or "off").lower()))
    if match := IGNORE.search(comment):
        rules = [r.strip() for r in (match.group("rules") or "").split(",") if r.strip()]
        local += [("pyright", rule) for rule in rules or ["*"]]
    elif match := PYRIGHT.search(comment):
        file += [("pyright", item.split("=")[0].strip()) for item in match.group("settings").split(",") if item.strip()]
    if match := PRAGMA.search(comment):
        local.append(("coverage", f"no {match.group('what').lower()}"))
    return local, file


def functions(root: Node) -> Functions:
    spans = []
    for node in walk(root):
        if node.type != "function_definition":
            continue
        name = text(node.child_by_field_name("name"))
        span = node.parent if node.parent and node.parent.type == "decorated_definition" else node
        owner = span.parent.parent if span.parent and span.parent.type == "block" else None
        if owner is not None and owner.type == "class_definition":
            name = f"{text(owner.child_by_field_name('name'))}.{name}"
        spans.append((span.start_byte, span.end_byte, name))
    return Functions(spans)


def declared(node: Node) -> Node | None:
    if node.type == "decorated_definition":
        node = node.child_by_field_name("definition") or node
    return node if node.type == "function_definition" else None


def aliases(root: Node) -> dict[str, str]:
    names = {module: module for module in MODULES}
    for node in walk(root):
        if node.type == "import_statement":
            for item in node.children_by_field_name("name"):
                if item.type == "aliased_import":
                    names[text(item.child_by_field_name("alias"))] = text(item.child_by_field_name("name"))
        elif node.type == "import_from_statement":
            module = text(node.child_by_field_name("module_name"))
            if module.split(".")[0] not in MODULES:
                continue
            for item in node.children_by_field_name("name"):
                name, alias = item, item
                if item.type == "aliased_import":
                    name, alias = item.child_by_field_name("name"), item.child_by_field_name("alias")
                names[text(alias)] = f"{module}.{text(name)}"
    return names


def resolve(node: Node | None, names: dict[str, str]) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return names.get(text(node))
    if node.type == "attribute":
        base = resolve(node.child_by_field_name("object"), names)
        return f"{base}.{text(node.child_by_field_name('attribute'))}" if base else None
    return None


def is_reference(node: Node) -> bool:
    parent = node.parent
    if parent is None or node.type != "identifier":
        return node.type == "attribute"
    if any(a.type in ("import_statement", "import_from_statement") for a in ancestors(node)):
        return False
    field = next((parent.field_name_for_child(i) for i, c in enumerate(parent.children) if c == node), None)
    return (parent.type, field) not in NOT_REFERENCES and (parent.type, None) not in NOT_REFERENCES


def ancestors(node: Node) -> list[Node]:
    found = []
    while node.parent is not None:
        node = node.parent
        found.append(node)
    return found


def syntax_rules(node: Node, names: dict[str, str]) -> list[tuple[str, str]]:
    if node.type in ("identifier", "attribute") and is_reference(node):
        marker = MARKERS.get(resolve(node, names) or "")
        return [marker] if marker else []
    if node.type == "call":
        function = node.child_by_field_name("function")
        if (call := CALLS.get(resolve(function, names) or "")) is not None:
            return [("pytest", call)]
        if function is not None and function.type == "attribute" and text(function.child_by_field_name("attribute")) == "skipTest":
            return [("unittest", "skipTest")]
    if node.type == "raise_statement" and len(node.named_children) > 0:
        raised = node.named_children[0]
        raised = raised.child_by_field_name("function") if raised.type == "call" else raised
        if resolve(raised, names) in SKIP_TEST:
            return [("unittest", "SkipTest")]
    return []


def whitelist(root: Node) -> list[Hit]:
    hits = []
    for statement in root.named_children:
        if statement.type == "expression_statement" and statement.named_child_count == 1:
            expression = statement.named_children[0]
            if expression.type in ("identifier", "attribute"):
                hits.append(("vulture", re.sub(r"\s+", "", text(expression)), ""))
    return hits


def is_comment(node: Node) -> bool:
    return node.type == "comment"


def extract(path: str, source: bytes) -> list[Hit]:
    root = parse(LANGUAGE, source)
    found = functions(root)
    names = aliases(root)
    hits: list[tuple[int, Hit]] = [(0, hit) for hit in generated(leading_comments(root, is_comment))]
    for node in walk(root):
        if node.type == "comment":
            comment = text(node)
            target = attached(node, is_comment, declared)
            function = found.at((target or node).start_byte)
            local, file = comment_rules(comment)
            hits += [(node.start_byte, (tool, rule, function)) for tool, rule in local]
            hits += [(node.start_byte, (tool, rule, "")) for tool, rule in file]
            hits += [(node.start_byte, hit) for hit in jscpd(comment, function)]
        else:
            rules = syntax_rules(node, names)
            hits += [(node.start_byte, (tool, rule, found.at(node.start_byte))) for tool, rule in rules]
    if "whitelist" in PurePosixPath(path).name.lower():
        hits += [(len(source), hit) for hit in whitelist(root)]
    return [hit for _, hit in sorted(hits, key=lambda item: item[0])]
