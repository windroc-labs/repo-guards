import tempfile
import tomllib
import unittest
from collections import Counter
from pathlib import Path

from allowlist import SECTION, TOOLS, Allowlist, AllowlistError, compare, load, render

ENTRY = """
[[exemption]]
tool = "ruff"
file = "src/m.py"
rule = "E501"
function = "f"
count = 2
reason = "long URL"
"""


def write(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def allowlist(text: str) -> Allowlist:
    return load(write(text))


def pasted(output: str) -> list[dict]:
    # The output must paste into repo-guards.toml as is; reading it back checks that.
    return tomllib.loads(output.replace(SECTION, "x"))["x"]["exemption"]


class LoadTest(unittest.TestCase):
    def test_default_settings_are_empty(self) -> None:
        loaded = load(Path(__file__).parent / "settings.toml")
        self.assertEqual((loaded.skip, loaded.entries), (frozenset(), {}))

    def test_entry(self) -> None:
        loaded = allowlist(ENTRY)
        self.assertEqual(loaded.entries, {("ruff", "src/m.py", "E501", "f"): (2, "long URL")})

    def test_s16_bad_entries(self) -> None:
        rows = {
            "多了字段 line": (ENTRY + "line = 3\n", "unknown field line"),
            "缺了字段 reason": (ENTRY.replace('reason = "long URL"\n', ""), "missing field reason"),
            "count = 0": (ENTRY.replace("count = 2", "count = 0"), "count"),
            'tool = "eslint-x"': (ENTRY.replace('tool = "ruff"', 'tool = "eslint-x"'), '"eslint-x"'),
            'reason = "   "': (ENTRY.replace('"long URL"', '"   "'), "reason"),
            "同一个键写了两条": (ENTRY + ENTRY, "exemption 2: same key as exemption 1"),
        }
        for row, (text, message) in rows.items():
            with self.subTest(row), self.assertRaises(AllowlistError) as raised:
                allowlist(text)
            self.assertIn("exemption", str(raised.exception))
            self.assertIn(message, str(raised.exception))

    def test_bad_types(self) -> None:
        for old, new in [("count = 2", "count = true"), ("count = 2", 'count = "2"'), ('file = "src/m.py"', "file = 1")]:
            with self.subTest(new), self.assertRaises(AllowlistError):
                allowlist(ENTRY.replace(old, new))

    def test_s45_skip_only_builtin_tools(self) -> None:
        with self.assertRaises(AllowlistError) as raised:
            allowlist('skip = ["cargo-test", "nosec"]\n')
        self.assertIn('"nosec"', str(raised.exception))

    def test_unknown_top_level_key_and_unreadable(self) -> None:
        with self.assertRaises(AllowlistError):
            allowlist("skips = []\n")
        with self.assertRaises(AllowlistError):
            allowlist("exemption = [\n")
        with self.assertRaises(AllowlistError):
            load(Path(tempfile.mkdtemp()) / "missing.toml")

    def test_tools(self) -> None:
        self.assertEqual(len(TOOLS), 27)
        self.assertIn("generated", TOOLS)


class CompareTest(unittest.TestCase):
    def test_s22_equal(self) -> None:
        self.assertEqual(compare(Counter({("ruff", "src/m.py", "E501", "f"): 2}), allowlist(ENTRY)), [])

    def test_s18_unregistered(self) -> None:
        changes = compare(Counter({("rustc", "src/a.rs", "dead_code", "f"): 1}), allowlist(""))
        self.assertEqual(
            pasted(render(changes)),
            [{"tool": "rustc", "file": "src/a.rs", "rule": "dead_code", "function": "f", "count": 1, "reason": ""}],
        )
        self.assertIn('[["ci-checks/inline-exemptions/settings.toml".exemption]]', render(changes))

    def test_s19_count_differs(self) -> None:
        output = render(compare(Counter({("ruff", "src/m.py", "E501", "f"): 3}), allowlist(ENTRY)))
        self.assertIn("# set count to 3 (was 2)", output)
        self.assertEqual(pasted(output)[0]["count"], 3)
        self.assertEqual(pasted(output)[0]["reason"], "long URL")

    def test_s20_registered_but_gone(self) -> None:
        text = ENTRY.replace('"ruff"', '"pytest"').replace("src/m.py", "tests/test_x.py")
        text = text.replace("E501", "skip").replace('"f"', '"test_y"')
        output = render(compare(Counter(), allowlist(text)))
        self.assertIn("# remove: no longer in the code", output)
        self.assertEqual(pasted(output)[0]["function"], "test_y")

    def test_s21_sorted_by_file_function_tool_rule(self) -> None:
        found = Counter(
            {
                ("ruff", "src/b.py", "E501", ""): 1,
                ("ruff", "src/a.py", "F401", "g"): 1,
                ("pyright", "src/a.py", "*", "g"): 1,
                ("ruff", "src/a.py", "E501", ""): 1,
            }
        )
        entries = [(e["file"], e["function"], e["tool"], e["rule"]) for e in pasted(render(compare(found, allowlist(""))))]
        self.assertEqual(entries, sorted(entries))
        self.assertEqual(entries[-1][0], "src/b.py")

    def test_s5_skipped_tool_hits_ignored(self) -> None:
        self.assertEqual(compare(Counter({("cargo-test", "src/lib.rs", "ignore", "t"): 1}), allowlist('skip = ["cargo-test"]\n')), [])

    def test_s46_skipped_tool_entry_must_go(self) -> None:
        text = 'skip = ["cargo-test"]\n' + ENTRY.replace('"ruff"', '"cargo-test"')
        found = Counter({("cargo-test", "src/m.py", "E501", "f"): 2})
        changes = compare(found, allowlist(text))
        self.assertEqual([c.kind for c in changes], ["remove"])

    def test_nfr4_no_workflow_command_lines(self) -> None:
        name = "::error::x.py\n::warning::y"
        output = render(compare(Counter({("ruff", name, "::E501", "::f"): 1}), allowlist("")))
        for line in output.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)
        self.assertEqual(pasted(output)[0]["file"], name)


if __name__ == "__main__":
    unittest.main()
