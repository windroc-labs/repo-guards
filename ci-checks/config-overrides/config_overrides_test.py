import datetime
import io
import json
import math
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from config_overrides import dump_toml, main

CI_CHECKS = Path(__file__).resolve().parents[1]


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Tree:
    def __init__(self, test: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "repo-guards"
        self.overrides = self.dir / "repo-guards.toml"

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def override(self, text: str) -> None:
        self.overrides.write_text(text, encoding="utf-8")

    def apply(self) -> tuple[int, str, str]:
        return run("apply", str(self.root), str(self.overrides))


class ApplyTest(unittest.TestCase):
    def test_toml_scalar_override(self) -> None:
        tree = Tree(self)
        target = tree.write("ci-checks/coverage/coverage.toml", "total-min = 80\ndiff-min = 90\n")
        tree.override('["ci-checks/coverage/coverage.toml"]\ntotal-min = 70\n')
        code, out, _ = tree.apply()
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(target.read_text()), {"total-min": 70, "diff-min": 90})
        self.assertIn("ci-checks/coverage/coverage.toml", out)

    def test_nested_tables_merge(self) -> None:
        tree = Tree(self)
        target = tree.write(
            "ci-checks/rust/deny.toml",
            '[advisories]\nyanked = "deny"\n[bans.build]\nallow-build-scripts = ["libc"]\n'
            '[sources]\nunknown-git = "deny"\nallow-git = []\n',
        )
        tree.override('["ci-checks/rust/deny.toml".sources]\nallow-git = ["https://x"]\n')
        self.assertEqual(tree.apply()[0], 0)
        self.assertEqual(
            tomllib.loads(target.read_text()),
            {
                "advisories": {"yanked": "deny"},
                "bans": {"build": {"allow-build-scripts": ["libc"]}},
                "sources": {"unknown-git": "deny", "allow-git": ["https://x"]},
            },
        )

    def test_lists_replace(self) -> None:
        tree = Tree(self)
        toml_target = tree.write("a.toml", '[bans.build]\nallow-build-scripts = ["libc", "syn"]\n')
        json_target = tree.write(
            "ci-checks/.jscpd.json", '{"minTokens": 50, "ignore": ["**/tests/**"]}'
        )
        tree.override(
            '["a.toml".bans.build]\nallow-build-scripts = ["a"]\n["ci-checks/.jscpd.json"]\nignore = []\n'
        )
        self.assertEqual(tree.apply()[0], 0)
        self.assertEqual(
            tomllib.loads(toml_target.read_text())["bans"]["build"]["allow-build-scripts"], ["a"]
        )
        self.assertEqual(json.loads(json_target.read_text()), {"minTokens": 50, "ignore": []})

    def test_type_mismatch_replaces(self) -> None:
        tree = Tree(self)
        target = tree.write("a.toml", "[x]\ny = 1\n")
        tree.override('["a.toml"]\nx = "flat"\n')
        self.assertEqual(tree.apply()[0], 0)
        self.assertEqual(tomllib.loads(target.read_text()), {"x": "flat"})

    def test_json_target(self) -> None:
        tree = Tree(self)
        target = tree.write(
            "ci-checks/typescript/knip.json",
            '{"$schema": "s", "entry": ["src/index.ts!"], "project": ["src/**/*.ts!"]}',
        )
        tree.override('["ci-checks/typescript/knip.json"]\nentry = ["src/main.ts!", "é"]\n')
        self.assertEqual(tree.apply()[0], 0)
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('  "entry"', text)
        self.assertIn("é", text)
        self.assertEqual(
            json.loads(text),
            {"$schema": "s", "entry": ["src/main.ts!", "é"], "project": ["src/**/*.ts!"]},
        )

    def test_empty_section_keeps_content(self) -> None:
        tree = Tree(self)
        target = tree.write("a.toml", "k = 1\n")
        tree.override('["a.toml"]\n')
        self.assertEqual(tree.apply()[0], 0)
        self.assertEqual(tomllib.loads(target.read_text()), {"k": 1})

    def test_missing_file_fails_and_writes_nothing(self) -> None:
        tree = Tree(self)
        good = tree.write("a.toml", "k = 1\n")
        tree.override('["a.toml"]\nk = 2\n["ci-checks/nope.toml"]\nk = 3\n')
        code, _, err = tree.apply()
        self.assertEqual(code, 2)
        self.assertIn('config_overrides: ["ci-checks/nope.toml"]: ', err)
        self.assertEqual(good.read_text(), "k = 1\n")

    def test_bad_sections_fail_named(self) -> None:
        tree = Tree(self)
        tree.write("a.yml", "k: 1\n")
        tree.write("broken.toml", "k = \n")
        tree.write("broken.json", "{")
        tree.write("dates.json", "{}")
        tree.override(
            'flat = 1\n["a.yml"]\nk = 2\n["broken.toml"]\nk = 2\n["broken.json"]\nk = 2\n'
            '["dates.json"]\nwhen = 2026-11-12\n'
        )
        code, _, err = tree.apply()
        self.assertEqual(code, 2)
        for section in ("flat", "a.yml", "broken.toml", "broken.json", "dates.json"):
            self.assertIn(f'config_overrides: ["{section}"]: ', err)
        self.assertEqual(len(err.strip().splitlines()), 5)

    def test_no_override_file(self) -> None:
        tree = Tree(self)
        target = tree.write("a.toml", "k = 1\n")
        code, out, _ = tree.apply()
        self.assertEqual(code, 0)
        self.assertIn("defaults", out)
        self.assertEqual(target.read_text(), "k = 1\n")

    def test_invalid_override_file(self) -> None:
        tree = Tree(self)
        tree.override("[[[")
        code, _, err = tree.apply()
        self.assertEqual(code, 2)
        self.assertIn(str(tree.overrides), err)

    def test_path_used_as_given(self) -> None:
        tree = Tree(self)
        outside = tree.dir / "outside.toml"
        outside.write_text("k = 1\n")
        tree.root.mkdir()
        tree.override('["../outside.toml"]\nk = 2\n')
        self.assertEqual(tree.apply()[0], 0)
        self.assertEqual(tomllib.loads(outside.read_text()), {"k": 2})

    def test_project_section_ignored(self) -> None:
        tree = Tree(self)
        tree.root.mkdir()
        tree.override('[project."."]\nsource-dirs = ["lib"]\n')
        self.assertEqual(tree.apply()[0], 0)

    def test_usage(self) -> None:
        self.assertEqual(run("apply", "x")[0], 2)
        self.assertEqual(run("nope")[0], 2)
        self.assertEqual(run()[0], 2)


class SourceDirsTest(unittest.TestCase):
    def overrides(self, text: str | None) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "repo-guards.toml"
        if text is not None:
            path.write_text(text)
        return str(path)

    def test_default_without_file(self) -> None:
        self.assertEqual(run("source-dirs", self.overrides(None), "."), (0, "src\n", ""))

    def test_listed(self) -> None:
        path = self.overrides('[project."crates/app"]\nsource-dirs = ["lib", "tools"]\n')
        self.assertEqual(run("source-dirs", path, "crates/app"), (0, "lib tools\n", ""))

    def test_other_working_directory_or_key_gets_default(self) -> None:
        path = self.overrides('[project."a"]\nsource-dirs = ["lib"]\n[project."b"]\n')
        self.assertEqual(run("source-dirs", path, ".")[1], "src\n")
        self.assertEqual(run("source-dirs", path, "b")[1], "src\n")


class GetTest(unittest.TestCase):
    def file(self, name: str, text: str) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text(text)
        return str(path)

    def test_scalar_and_nested(self) -> None:
        path = self.file("t.toml", '[toolchain]\nchannel = "1.99.1"\n[x]\nflag = true\nn = 3\n')
        self.assertEqual(run("get", path, "toolchain", "channel"), (0, "1.99.1\n", ""))
        self.assertEqual(run("get", path, "x", "flag")[1], "true\n")
        self.assertEqual(run("get", path, "x", "n")[1], "3\n")

    def test_list_one_per_line(self) -> None:
        path = self.file("l.toml", 'exceptions = ["a", "b"]\nempty = []\n')
        self.assertEqual(run("get", path, "exceptions")[1], "a\nb\n")
        self.assertEqual(run("get", path, "empty"), (0, "", ""))

    def test_json(self) -> None:
        path = self.file("c.json", '{"exclude": ["**/*.d.ts"]}')
        self.assertEqual(run("get", path, "exclude")[1], "**/*.d.ts\n")

    def test_missing_key_or_file(self) -> None:
        path = self.file("t.toml", "a = 1\n")
        self.assertEqual(run("get", path, "b")[0], 2)
        self.assertEqual(run("get", path, "a", "b")[0], 2)
        self.assertEqual(run("get", path + ".missing", "a")[0], 2)
        self.assertEqual(run("get", path)[0], 2)


class DumpTomlTest(unittest.TestCase):
    def assertRoundTrip(self, table: dict) -> None:
        again = tomllib.loads(dump_toml(table))
        self.assertEqual(again, table)

    def test_repo_tomls_round_trip(self) -> None:
        files = sorted(CI_CHECKS.rglob("*.toml"))
        self.assertGreater(len(files), 5)
        for path in files:
            with self.subTest(path=str(path.relative_to(CI_CHECKS))):
                self.assertRoundTrip(tomllib.loads(path.read_text(encoding="utf-8")))

    def test_value_types_round_trip(self) -> None:
        self.assertRoundTrip(
            {
                "s": 'q"uote\\back\nline\ttab\x01ctl é 😀',
                "n": -3,
                "f": 1.5,
                "b": False,
                "dt": datetime.datetime(2026, 11, 12, 1, 2, 3, tzinfo=datetime.UTC),
                "local": datetime.datetime(2026, 11, 12, 1, 2, 3, 500000),
                "d": datetime.date(2026, 11, 12),
                "t": datetime.time(1, 2, 3),
                "inf": math.inf,
                "ninf": -math.inf,
                "key with space": {"a.b": 1},
                "": "empty key",
                "list": [1, [2, 3], {"inline": {"deep": True}}],
                "aot": [{"name": "a"}, {"name": "b", "sub": {"x": 1}}],
                "empty": {},
                "only": {"sub": {"empty": {}}},
            }
        )

    def test_nan(self) -> None:
        self.assertTrue(math.isnan(tomllib.loads(dump_toml({"x": math.nan}))["x"]))

    def test_empty_table_keeps_header(self) -> None:
        self.assertIn("[licenses]", dump_toml({"licenses": {}}))


if __name__ == "__main__":
    unittest.main()
