import contextlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[3] / "guards/ci-checks/api-snapshot"
sys.path.insert(0, str(SOURCE))

from api_snapshot import Diff, Entry, Error, compare, main, parse, render


OPEN = Entry("pkg", "", "function", "open", "def open(path: str) -> int")
CLOSE = Entry("pkg", "", "function", "close", "def close() -> None")
METHOD = Entry("pkg", "File", "method", "read", "def read(self) -> bytes")


def as_json(*entries: Entry) -> str:
    return json.dumps([e._asdict() for e in entries])


def keyed(*entries: Entry) -> dict:
    return {e.key: e for e in entries}


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
            [
                ("a", "", "function", "b"),
                ("a", "", "function", "z"),
                ("a", "", "type", "T"),
                ("a", "T", "method", "a"),
                ("b", "", "function", "a"),
            ],
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
        self.assertEqual(diff.changed, sorted(zip(changed_old, changed_new, strict=True)))


class Run:
    """One run of main() in a temp dir: entries and snapshot in, exit code, log, summary and output out."""

    def __init__(
        self,
        case: unittest.TestCase,
        entries,
        snapshot=None,
        summary=True,
        artifact="api-snapshot-rust",
        path=".github/checks/api-rust.json",
    ):
        tmp = Path(case.enterContext(tempfile.TemporaryDirectory()))
        self.entries = tmp / "entries.json"
        self.entries.write_text(entries if isinstance(entries, str) else as_json(*entries), encoding="utf-8")
        self.snapshot = tmp / "snapshot.json"
        if snapshot is not None:
            self.snapshot.write_text(snapshot if isinstance(snapshot, str) else as_json(*snapshot), encoding="utf-8")
        self.output = tmp / "out" / "new.json"
        self.output.parent.mkdir()
        self.summary = tmp / "summary.md"
        self.env = {"GITHUB_STEP_SUMMARY": str(self.summary)} if summary else {}
        self.argv = [
            "--entries",
            str(self.entries),
            "--snapshot",
            str(self.snapshot),
            "--output",
            str(self.output),
            "--artifact",
            artifact,
            "--path",
            path,
        ]

    def __call__(self) -> "Run":
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"} | self.env
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            self.code = main(self.argv)
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        self.summary_text = self.summary.read_bytes().decode("utf-8") if self.summary.is_file() else ""
        return self


def fenced(summary: str) -> tuple[str, str]:
    """Split a summary into text inside fenced code blocks and text outside them, per CommonMark line endings."""
    inside, outside, fence = [], [], None
    for line in re.split(r"\r\n|\r|\n", summary):
        if fence is None:
            opening = re.fullmatch(r"(`{3,})text", line)
            if opening:
                fence = opening.group(1)
            else:
                outside.append(line)
        elif re.fullmatch(" {0,3}" + fence + "`*[ \t]*", line):
            fence = None
        else:
            inside.append(line)
    assert fence is None, "unclosed fence"
    return "\n".join(inside), "\n".join(outside)


class Main(unittest.TestCase):
    def test_match_exits_0_and_writes_nothing(self):
        run = Run(self, [OPEN, METHOD], [METHOD, OPEN])()
        self.assertEqual(run.code, 0, run.stderr)
        self.assertIn("matches", run.stdout)
        self.assertIn("一致", run.summary_text)
        self.assertNotIn("不一致", run.summary_text)
        self.assertFalse(run.output.exists())

    def test_added_function_exits_1_listed_under_added(self):
        run = Run(self, [OPEN, CLOSE], [OPEN])()
        self.assertEqual(run.code, 1)
        self.assertIn(
            '多 {"unit": "pkg", "parent": "", "kind": "function", "name": "close"} "def close() -> None"',
            run.stdout.splitlines(),
        )
        self.assertIn("#### 多", run.summary_text)
        self.assertIn("name: close\nsignature: def close() -> None", run.summary_text)

    def test_new_unit_is_added(self):
        other = OPEN._replace(unit="pkg2")
        run = Run(self, [OPEN, other], [OPEN])()
        self.assertEqual(run.code, 1)
        self.assertIn("#### 多", run.summary_text)
        self.assertIn("unit: pkg2", run.summary_text)

    def test_removed_function_exits_1_listed_under_removed(self):
        run = Run(self, [OPEN], [OPEN, CLOSE])()
        self.assertEqual(run.code, 1)
        self.assertTrue(any(line.startswith("少 ") and '"close"' in line for line in run.stdout.splitlines()))
        self.assertIn("#### 少", run.summary_text)
        self.assertNotIn("#### 多", run.summary_text)

    def test_return_type_change_is_changed(self):
        new = OPEN._replace(signature="def open(path: str) -> str")
        run = Run(self, [new], [OPEN])()
        self.assertEqual(run.code, 1)
        self.assertIn("#### 变", run.summary_text)
        self.assertNotIn("#### 多", run.summary_text)
        self.assertNotIn("#### 少", run.summary_text)

    def test_changed_signature_lists_old_and_new(self):
        new = OPEN._replace(signature="def open(path: Path) -> int")
        run = Run(self, [new], [OPEN])()
        self.assertEqual(run.code, 1)
        lines = run.stdout.splitlines()
        at = lines.index('变 {"unit": "pkg", "parent": "", "kind": "function", "name": "open"}')
        self.assertEqual(
            lines[at + 1 : at + 3], ['  旧 "def open(path: str) -> int"', '  新 "def open(path: Path) -> int"']
        )
        self.assertIn("旧签名: def open(path: str) -> int\n新签名: def open(path: Path) -> int", run.summary_text)

    def test_mismatch_writes_full_snapshot_and_names_artifact_and_path(self):
        run = Run(self, [OPEN, CLOSE, METHOD], [OPEN])()
        self.assertEqual(run.code, 1)
        self.assertEqual(run.output.read_text(encoding="utf-8"), render({e.key: e for e in (OPEN, CLOSE, METHOD)}))
        inside, _ = fenced(run.summary_text)
        self.assertIn("api-snapshot-rust", inside)
        self.assertIn(".github/checks/api-rust.json", inside)
        self.assertIn(
            'new snapshot: artifact "api-snapshot-rust", commit it as ".github/checks/api-rust.json"',
            run.stdout.splitlines(),
        )

    def test_written_snapshot_then_matches(self):
        first = Run(self, [METHOD, OPEN], [CLOSE])()
        self.assertEqual(first.code, 1)
        second = Run(self, [OPEN, METHOD], first.output.read_text(encoding="utf-8"))()
        self.assertEqual(second.code, 0, second.stderr)

    def test_missing_snapshot_exits_1_writes_snapshot_names_artifact_and_path(self):
        run = Run(self, [OPEN, CLOSE])()
        self.assertEqual(run.code, 1)
        self.assertEqual(run.output.read_text(encoding="utf-8"), render({e.key: e for e in (OPEN, CLOSE)}))
        self.assertFalse(run.snapshot.exists())
        self.assertIn('".github/checks/api-rust.json" does not exist; 2 entries generated', run.stdout)
        self.assertIn("快照文件不存在", run.summary_text)
        inside, _ = fenced(run.summary_text)
        self.assertIn("api-snapshot-rust", inside)
        self.assertIn(".github/checks/api-rust.json", inside)
        self.assertNotIn("open", run.stdout + run.summary_text)

    def test_invalid_json_snapshot_exits_2(self):
        run = Run(self, [OPEN], "{not json")()
        self.assertEqual(run.code, 2)
        self.assertIn("snapshot: not valid JSON", run.stderr)
        self.assertFalse(run.output.exists())
        self.assertEqual(run.summary_text, "")

    def test_wrong_shape_snapshot_exits_2(self):
        run = Run(self, [OPEN], json.dumps({"entries": []}))()
        self.assertEqual(run.code, 2)
        self.assertFalse(run.output.exists())

    def test_duplicate_key_in_entries_exits_2(self):
        run = Run(self, [OPEN, OPEN._replace(signature="x")], [OPEN])()
        self.assertEqual(run.code, 2)
        self.assertIn("duplicate key", run.stderr)
        self.assertFalse(run.output.exists())

    def test_unreadable_inputs_exit_2(self):
        missing_entries = Run(self, [OPEN], [OPEN])
        missing_entries.entries.unlink()
        snapshot_is_dir = Run(self, [OPEN])
        snapshot_is_dir.snapshot.mkdir()
        not_utf8 = Run(self, [OPEN], [OPEN])
        not_utf8.snapshot.write_bytes(b"\xff\xfe[]")
        for label, run in {
            "missing entries": missing_entries,
            "snapshot is a directory": snapshot_is_dir,
            "snapshot not UTF-8": not_utf8,
        }.items():
            with self.subTest(label):
                run()
                self.assertEqual(run.code, 2)
                self.assertTrue(run.stderr.startswith("api snapshot: "))
                self.assertFalse(run.output.exists())

    def test_unwritable_output_exits_2(self):
        run = Run(self, [OPEN, CLOSE], [OPEN])
        run.output.mkdir()
        run()
        self.assertEqual(run.code, 2)
        self.assertIn("cannot write", run.stderr)

    def test_text_not_encodable_as_utf8_exits_2_and_leaves_no_file(self):
        lone_surrogate = '[{"unit": "u", "parent": "", "kind": "k", "name": "n", "signature": "\\ud800"}]'
        run = Run(self, lone_surrogate)()
        self.assertEqual(run.code, 2)
        self.assertIn("not valid UTF-8", run.stderr)
        self.assertFalse(run.output.exists())
        self.assertEqual(run.stdout, "")

    def test_failed_write_prints_no_new_snapshot_line(self):
        run = Run(self, [OPEN, CLOSE], [OPEN])
        run.output.mkdir()
        run()
        self.assertEqual(run.code, 2)
        self.assertNotIn("new snapshot", run.stdout)

    def test_unwritable_summary_exits_2(self):
        run = Run(self, [OPEN, CLOSE], [OPEN])
        run.summary.mkdir()
        run()
        self.assertEqual(run.code, 2)
        self.assertIn("cannot write", run.stderr)

    def test_bad_arguments_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--entries", "x"])
        self.assertEqual(caught.exception.code, 2)

    def test_snapshot_file_never_written(self):
        for label, entries in {"match": [OPEN], "mismatch": [OPEN, CLOSE]}.items():
            with self.subTest(label):
                run = Run(self, entries, [OPEN])
                os.utime(run.snapshot, ns=(1_000_000_000, 1_000_000_000))
                before = run.snapshot.read_bytes(), run.snapshot.stat().st_mtime_ns
                run()
                self.assertEqual((run.snapshot.read_bytes(), run.snapshot.stat().st_mtime_ns), before)

    def test_runs_without_step_summary(self):
        run = Run(self, [OPEN, CLOSE], [OPEN], summary=False)()
        self.assertEqual(run.code, 1)
        self.assertFalse(run.summary.exists())
        self.assertIn("多 ", run.stdout)

    def test_summary_is_appended(self):
        run = Run(self, [OPEN], [OPEN])
        run.summary.write_text("earlier step\n", encoding="utf-8")
        run()
        self.assertTrue(run.summary_text.startswith("earlier step\n### 接口快照"))

    def test_unexpected_errors_exit_2(self):
        cases = {
            "lone surrogate cannot be written as UTF-8": (
                '[{"unit": "u", "parent": "", "kind": "k", "name": "n", "signature": "\\ud800"}]',
                "[]",
            ),
            "nesting too deep": ("[" * 200_000 + "]" * 200_000, "[]"),
        }
        for label, (entries, snapshot) in cases.items():
            with self.subTest(label):
                run = Run(self, entries, snapshot)
                env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
                result = subprocess.run(
                    [sys.executable, str(SOURCE / "api_snapshot.py"), *run.argv],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr.startswith("api snapshot: "), result.stderr)
                self.assertNotIn("Traceback", result.stderr)


ATTACKS = [
    "::error::x",
    "##[error]x",
    "[click](https://example.com)",
    "a | b",
    "line1\n::warning::line2",
    "cr\r::notice::x",
    "``` fence",
    "x\n```\ny",
    "````",
]


class Escaping(unittest.TestCase):
    def runs(self):
        for text in ATTACKS:
            added = Entry("pkg", "", "function", f"added {text}", f"sig {text}")
            removed = Entry("pkg", text, "function", "gone", f"sig {text}")
            kept = Entry("pkg", "", "function", "kept", "old")
            yield (
                text,
                Run(self, [added, kept._replace(signature=f"new {text}")], [removed, kept], artifact=text, path=text)(),
            )

    def assert_log_safe(self, *streams: str):
        for stream in streams:
            for line in stream.split("\n"):
                self.assertFalse(line.startswith("::"), line)
                self.assertNotIn("##[", line)
                self.assertNotIn("\r", line)

    def test_log_has_no_workflow_command_lines(self):
        for text, run in self.runs():
            with self.subTest(text=text):
                self.assertEqual(run.code, 1)
                self.assertEqual(sum(line.startswith(("多 ", "少 ", "变 ")) for line in run.stdout.splitlines()), 3)
                self.assert_log_safe(run.stdout, run.stderr)

    def test_error_messages_have_no_workflow_command_lines(self):
        for text in ATTACKS:
            bad = Entry(text, text, text, text, text)
            for label, entries, snapshot in [
                ("duplicate key", [bad, bad], []),
                ("extra field name", [OPEN], json.dumps([{**OPEN._asdict(), text: "y"}])),
            ]:
                with self.subTest(text=text, case=label):
                    run = Run(self, entries, snapshot, artifact=text, path=text)()
                    self.assertEqual(run.code, 2)
                    self.assert_log_safe(run.stdout, run.stderr)

    def test_summary_shows_text_in_code_fence(self):
        for text, run in self.runs():
            with self.subTest(text=text):
                inside, outside = fenced(run.summary_text)
                # fenced() splits on every CommonMark line ending, so compare with CR line endings as LF.
                shown = re.sub(r"\r\n|\r", "\n", text)
                for value in (f"added {shown}", f"sig {shown}", f"new {shown}", f"parent: {shown}"):
                    self.assertIn(value, inside)
                self.assertIn(f"\n{shown}\n", f"\n{inside}\n")
                self.assertIn(text, run.summary_text)
                for part in re.split(r"\r\n|\r|\n", text):
                    if part:
                        self.assertNotIn(part, outside)


class Summary(unittest.TestCase):
    def test_category_order_and_empty_omitted(self):
        changed = OPEN._replace(signature="new")
        run = Run(self, [changed, METHOD], [OPEN, CLOSE])()
        headings = [line for line in run.summary_text.splitlines() if line.startswith("#### ")]
        self.assertEqual(headings, ["#### 多", "#### 少", "#### 变"])
        self.assertIn("多 1 条、少 1 条、变 1 条", run.summary_text)
        only_removed = Run(self, [OPEN], [OPEN, CLOSE])()
        self.assertEqual(
            [line for line in only_removed.summary_text.splitlines() if line.startswith("#### ")], ["#### 少"]
        )

    def test_entries_in_a_category_follow_key_order(self):
        names = ["c", "a", "b"]
        run = Run(self, [Entry("u", "", "k", n, "s") for n in names], [])()
        inside, _ = fenced(run.summary_text)
        self.assertEqual(re.findall(r"^name: (.)$", inside, re.M), ["a", "b", "c"])
        self.assertEqual(
            [line.split('"name": ')[1][:3] for line in run.stdout.splitlines() if line.startswith("多 ")],
            ['"a"', '"b"', '"c"'],
        )


if __name__ == "__main__":
    unittest.main()
