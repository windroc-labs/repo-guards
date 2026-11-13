import json
import random
import unittest

from api_snapshot import Diff, Entry, Error, compare, parse, render

OPEN = Entry("pkg", "", "function", "open", "def open(path: str) -> int")
CLOSE = Entry("pkg", "", "function", "close", "def close() -> None")
METHOD = Entry("pkg", "File", "method", "read", "def read(self) -> bytes")


def as_json(*entries: Entry) -> str:
    return json.dumps([e._asdict() for e in entries])


def keyed(*entries: Entry) -> dict:
    return {e[:4]: e for e in entries}


class Parse(unittest.TestCase):
    def test_valid_entries_keyed_by_unit_parent_kind_name(self):
        self.assertEqual(parse(as_json(OPEN, METHOD), "entries"), keyed(OPEN, METHOD))

    def test_field_order_in_the_input_does_not_matter(self):
        item = {"signature": "s", "name": "n", "kind": "k", "parent": "p", "unit": "u"}
        self.assertEqual(parse(json.dumps([item]), "entries"), keyed(Entry("u", "p", "k", "n", "s")))

    def test_invalid_json_names_position_not_text(self):
        with self.assertRaises(Error) as caught:
            parse('[{"unit": "##[error]x"', "snapshot")
        message = str(caught.exception)
        self.assertIn("snapshot", message)
        self.assertIn("line 1", message)
        self.assertNotIn("##[", message)

    def test_wrong_shapes(self):
        good = OPEN._asdict()
        shapes = {
            "top level object": json.dumps({"entries": []}),
            "item not object": json.dumps(["x"]),
            "missing field": json.dumps([{k: v for k, v in good.items() if k != "signature"}]),
            "extra field": json.dumps([{**good, "doc": "x"}]),
            "non-string value": json.dumps([{**good, "parent": None}]),
            "number value": json.dumps([{**good, "name": 1}]),
        }
        for label, text in shapes.items():
            with self.subTest(label), self.assertRaises(Error) as caught:
                parse(text, "snapshot")
            self.assertIn("snapshot", str(caught.exception))

    def test_duplicate_key_names_the_key(self):
        twin = OPEN._replace(signature="def open() -> str")
        with self.assertRaises(Error) as caught:
            parse(as_json(CLOSE, OPEN, twin), "entries")
        message = str(caught.exception)
        self.assertIn("duplicate", message)
        self.assertIn('"open"', message)
        self.assertIn("entries", message)

    def test_messages_quote_untrusted_text(self):
        bad = Entry("u", "##[error]x", "k", "n\n::error::y", "s")
        cases = {
            "duplicate": as_json(bad, bad),
            "extra field name": json.dumps([{**OPEN._asdict(), "##[error]x": "y"}]),
            "extra field name with newline": json.dumps([{**OPEN._asdict(), "a\n::error::x": "y"}]),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(Error) as caught:
                parse(text, "snapshot")
            message = str(caught.exception)
            self.assertNotIn("##[", message)
            self.assertNotIn("\n", message)


class Render(unittest.TestCase):
    def test_sorted_by_unit_parent_kind_name(self):
        entries = [
            Entry("b", "", "function", "a", "s"),
            Entry("a", "T", "method", "a", "s"),
            Entry("a", "", "type", "T", "s"),
            Entry("a", "", "function", "z", "s"),
            Entry("a", "", "function", "b", "s"),
        ]
        order = [(e["unit"], e["parent"], e["kind"], e["name"]) for e in json.loads(render(keyed(*entries)))]
        self.assertEqual(
            order,
            [("a", "", "function", "b"), ("a", "", "function", "z"), ("a", "", "type", "T"), ("a", "T", "method", "a"),
             ("b", "", "function", "a")],
        )

    def test_byte_identical_for_any_input_order(self):
        entries = [Entry(f"u{i % 3}", f"p{i % 2}", "k", f"n{i}", f"s{i}") for i in range(20)]
        first = render(keyed(*entries))
        for seed in range(5):
            shuffled = entries[:]
            random.Random(seed).shuffle(shuffled)
            with self.subTest(seed=seed):
                self.assertEqual(render(keyed(*shuffled)), first)

    def test_exact_layout(self):
        entry = Entry("pkg", "", "function", "名", 'def f(x: str = "é") -> None')
        self.assertEqual(
            render(keyed(entry)),
            "[\n"
            "  {\n"
            '    "unit": "pkg",\n'
            '    "parent": "",\n'
            '    "kind": "function",\n'
            '    "name": "名",\n'
            '    "signature": "def f(x: str = \\"é\\") -> None"\n'
            "  }\n"
            "]\n",
        )

    def test_empty(self):
        self.assertEqual(render({}), "[]\n")

    def test_round_trip(self):
        entries = keyed(OPEN, CLOSE, METHOD, Entry("u", "", "k", "line\nbreak", "tab\there"))
        self.assertEqual(parse(render(entries), "snapshot"), entries)


class Compare(unittest.TestCase):
    def test_match(self):
        diff = compare(keyed(OPEN, METHOD), keyed(METHOD, OPEN))
        self.assertTrue(diff.empty)
        self.assertEqual(diff, Diff([], [], []))

    def test_added_removed_changed(self):
        changed = OPEN._replace(signature="def open(path: str) -> str")
        diff = compare(keyed(OPEN, CLOSE), keyed(changed, METHOD))
        self.assertFalse(diff.empty)
        self.assertEqual(diff.added, [METHOD])
        self.assertEqual(diff.removed, [CLOSE])
        self.assertEqual(diff.changed, [(OPEN, changed)])

    def test_same_count_different_keys_fails(self):
        diff = compare(keyed(OPEN), keyed(CLOSE))
        self.assertEqual((diff.added, diff.removed), ([CLOSE], [OPEN]))

    def test_new_unit_is_added(self):
        other = OPEN._replace(unit="pkg2")
        self.assertEqual(compare(keyed(OPEN), keyed(OPEN, other)).added, [other])

    def test_each_category_sorted_by_key(self):
        new = [Entry("u", "", "k", n, "s") for n in "dcba"]
        old = [Entry("v", "", "k", n, "s") for n in "zyx"]
        changed_old = [Entry("w", "", "k", n, "old") for n in "nm"]
        changed_new = [e._replace(signature="new") for e in changed_old]
        diff = compare(keyed(*old, *changed_old), keyed(*new, *changed_new))
        self.assertEqual(diff.added, sorted(new))
        self.assertEqual(diff.removed, sorted(old))
        self.assertEqual(diff.changed, sorted(zip(changed_old, changed_new)))


if __name__ == "__main__":
    unittest.main()
