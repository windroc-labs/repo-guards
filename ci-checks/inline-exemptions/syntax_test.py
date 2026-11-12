import unittest

import tree_sitter as ts
import tree_sitter_python

from syntax import Functions, SourceError, attached, parse, walk

LANGUAGE = ts.Language(tree_sitter_python.language())


def declared(node: ts.Node) -> ts.Node | None:
    return node if node.type == "function_definition" else None


class SyntaxTest(unittest.TestCase):
    def test_innermost_span(self) -> None:
        functions = Functions([(0, 100, "outer"), (10, 20, "inner"), (30, 40, "other")])
        self.assertEqual([functions.at(b) for b in (5, 15, 25, 35, 100)], ["outer", "inner", "outer", "other", ""])

    def test_syntax_error_line(self) -> None:
        with self.assertRaises(SourceError) as raised:
            parse(LANGUAGE, b"x = 1\ndef f(:\n")
        self.assertEqual(str(raised.exception), "syntax error at line 2")

    def test_attached_needs_adjacent_own_line(self) -> None:
        root = parse(LANGUAGE, b"# a\n# b\ndef f():\n    pass\n\n# c\n\ndef g():\n    pass\nx = 1  # d\ndef h():\n    pass\n")
        comments = [n for n in walk(root) if n.type == "comment"]
        targets = [attached(c, lambda n: n.type == "comment", declared) for c in comments]
        self.assertEqual([t.child_by_field_name("name").text if t else None for t in targets], [b"f", b"f", None, None])


if __name__ == "__main__":
    unittest.main()
