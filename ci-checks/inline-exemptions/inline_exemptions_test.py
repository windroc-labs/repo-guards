import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import inline_exemptions as ie

ADD_HEADER = '[["ci-checks/inline-exemptions/settings.toml".exemption]]'


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def entry(tool, file, rule, function, count=1, reason="why"):
    return {"tool": tool, "file": file, "rule": rule, "function": function, "count": count, "reason": reason}


def toml_entry(fields):
    lines = [ADD_HEADER]
    for key, value in fields.items():
        lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines)


class Repo:
    """A committed git repo in a temp dir, plus a settings file beside it."""

    def __init__(self, test, files, skip=(), exemptions=(), settings_text=None):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "ci@example.com")
        git(self.root, "config", "user.name", "ci")
        for path, text in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                target.write_bytes(text)
            else:
                target.write_text(text)
        if files:
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", "init")
        self.settings = Path(tmp.name) / "settings.toml"
        if settings_text is None:
            settings_text = f"skip = {json.dumps(list(skip))}\n" + "".join(
                "\n" + toml_entry(e).replace(ADD_HEADER, "[[exemption]]") + "\n" for e in exemptions
            )
            if not exemptions:
                settings_text += "exemption = []\n"
        self.settings.write_text(settings_text)


class FakeScan:
    """Stands in for the language scanners: returns the given records for each language."""

    def __init__(self, records=None, crash=None):
        self.records = records or {}
        self.crash = crash
        self.calls = {}

    def __call__(self, language, root, files):
        self.calls[language] = list(files)
        if language == self.crash:
            raise ie.Error(f"{language} scanner exited with 101")
        return "".join(json.dumps(r) + "\n" for r in self.records.get(language, []))


def occurrence(tool, file, rule, function):
    return {"tool": tool, "file": file, "rule": rule, "function": function}


def run(repo, scan):
    out = io.StringIO()
    code = ie.check(repo.root, repo.settings, scan, out)
    return code, out.getvalue()


class Files(unittest.TestCase):
    def test_s2_unchanged_files_are_checked(self):
        repo = Repo(self, {"src/old.rs": "#[allow(dead_code)]\nfn f() {}\n", "README.md": "a\n"})
        (repo.root / "README.md").write_text("b\n")
        git(repo.root, "commit", "-q", "-am", "readme only")
        scan = FakeScan({"rust": [occurrence("rustc", "src/old.rs", "dead_code", "f")]})
        code, out = run(repo, scan)
        self.assertEqual(code, 1)
        self.assertIn('file = "src/old.rs"', out)
        self.assertEqual(scan.calls["rust"], ["src/old.rs"])

    def test_s3_submodule_contents_are_not_scanned(self):
        repo = Repo(self, {"src/m.py": "x = 1\n"})
        sha = subprocess.run(
            ["git", "-C", str(repo.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        git(repo.root, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/lib")
        git(repo.root, "commit", "-q", "-m", "submodule")
        scan = FakeScan()
        code, out = run(repo, scan)
        self.assertEqual(code, 0)
        self.assertNotIn("vendor/lib", out)
        self.assertEqual(scan.calls, {"python": ["src/m.py"]})

    def test_s30_s12_only_listed_files_go_to_their_scanner(self):
        repo = Repo(
            self,
            {
                "stubs/x.pyi": "x: int  # type: ignore\n",
                "types/y.d.ts": "// @ts-ignore\n",
                "assets/logo.png": b"\x89PNG\xff\xfe",
                "docs/a.md": "<!-- prettier-ignore -->\n",
                ".github/checks/repo-guards.toml": '["ci-checks/.jscpd.json"]\nignorePattern = ["x"]\n',
                "a/Cargo.toml": "[package]\n",
                "pyproject.toml": "[project]\n",
                "tools/go.mod": "module x\n",
                "src/a.mts": "",
                "src/b.cjs": "",
                "src/c.jsx": "",
                "pkg/a.go": "package a\n",
                "src/lib.rs": "",
            },
        )
        scan = FakeScan()
        code, out = run(repo, scan)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            scan.calls,
            {
                "rust": ["a/Cargo.toml", "src/lib.rs"],
                "python": ["pyproject.toml", "stubs/x.pyi"],
                "go": ["pkg/a.go", "tools/go.mod"],
                "typescript": ["src/a.mts", "src/b.cjs", "src/c.jsx", "types/y.d.ts"],
            },
        )
        self.assertNotIn("repo-guards.toml", out)

    def test_s49_symlinked_source_file_fails(self):
        repo = Repo(self, {"assets/util.txt": "x = 1\n"})
        os.symlink("../assets/util.txt", repo.root / "src_util_link")
        (repo.root / "src").mkdir()
        os.symlink("../assets/util.txt", repo.root / "src/util.py")
        git(repo.root, "add", "-A")
        git(repo.root, "commit", "-q", "-m", "links")
        code, out = run(repo, FakeScan())
        self.assertEqual(code, 2)
        self.assertIn('"src/util.py"', out)
        self.assertNotIn("src_util_link", out)

    def test_s33_uncommitted_changes_print_a_notice_first(self):
        repo = Repo(self, {"src/m.py": "x = 1\n"})
        (repo.root / "src/m.py").write_text("x = 2  # noqa: E501\n")
        scan = FakeScan({"python": [occurrence("ruff", "src/m.py", "E501", "")]})
        code, out = run(repo, scan)
        self.assertEqual(code, 1)
        self.assertIn("uncommitted", out.splitlines()[0])

    def test_clean_tree_has_no_notice(self):
        repo = Repo(self, {"src/m.py": "x = 1\n"})
        _, out = run(repo, FakeScan())
        self.assertNotIn("uncommitted", out)

    def test_tracked_file_deleted_from_work_tree_is_skipped(self):
        repo = Repo(self, {"src/m.py": "x = 1\n", "src/n.py": "y = 1\n"})
        (repo.root / "src/n.py").unlink()
        scan = FakeScan()
        run(repo, scan)
        self.assertEqual(scan.calls, {"python": ["src/m.py"]})

    def test_languages(self):
        repo = Repo(self, {"src/m.py": "", "Cargo.toml": "[package]\n", "README.md": ""})
        self.assertEqual(
            ie.languages(repo.root), {"rust": True, "python": True, "go": False, "typescript": False}
        )


class Skip(unittest.TestCase):
    def test_s5_skipped_tool_is_not_checked(self):
        repo = Repo(self, {"src/lib.rs": "#[ignore]\nfn t() {}\n"}, skip=["cargo-test"])
        code, out = run(repo, FakeScan({"rust": [occurrence("cargo-test", "src/lib.rs", "ignore", "t")]}))
        self.assertEqual(code, 0, out)

    def test_s45_unknown_tool_in_skip_fails(self):
        repo = Repo(self, {"src/m.py": ""}, skip=["nosec"])
        code, out = run(repo, FakeScan())
        self.assertEqual(code, 2)
        self.assertIn('"nosec"', out)

    def test_s46_entry_of_a_skipped_tool_must_be_removed(self):
        repo = Repo(
            self,
            {"src/lib.rs": "#[ignore]\nfn t() {}\n"},
            skip=["cargo-test"],
            exemptions=[entry("cargo-test", "src/lib.rs", "ignore", "t")],
        )
        code, out = run(repo, FakeScan({"rust": [occurrence("cargo-test", "src/lib.rs", "ignore", "t")]}))
        self.assertEqual(code, 1)
        self.assertIn("# remove", out)
        self.assertIn('tool = "cargo-test"', out)


class Allowlist(unittest.TestCase):
    def assert_bad(self, exemptions, needle, settings_text=None):
        repo = Repo(self, {"src/m.py": ""}, exemptions=exemptions, settings_text=settings_text)
        code, out = run(repo, FakeScan())
        self.assertEqual(code, 2, out)
        self.assertIn(needle, out)
        return out

    def test_s16_extra_field(self):
        bad = entry("ruff", "src/m.py", "E501", "f") | {"line": 3}
        self.assert_bad([bad], '"line"')

    def test_s16_missing_field(self):
        bad = entry("ruff", "src/m.py", "E501", "f")
        del bad["reason"]
        self.assert_bad([bad], '"reason"')

    def test_s16_count_zero(self):
        self.assert_bad([entry("ruff", "src/m.py", "E501", "f", count=0)], "count")

    def test_s16_unknown_tool(self):
        self.assert_bad([entry("eslint-x", "src/m.py", "E501", "f")], '"eslint-x"')

    def test_s16_blank_reason(self):
        self.assert_bad([entry("ruff", "src/m.py", "E501", "f", reason="   ")], "reason")

    def test_s16_duplicate_key(self):
        e = entry("ruff", "src/m.py", "E501", "f")
        out = self.assert_bad([e, e | {"count": 2}], "#2")
        self.assertIn("#1", out)

    def test_wrong_types(self):
        self.assert_bad([entry("ruff", "src/m.py", "E501", "f", count=True)], "count")
        self.assert_bad([entry("ruff", "src/m.py", 5, "f")], '"rule"')

    def test_unknown_settings_key_and_bad_toml(self):
        self.assert_bad([], '"other"', settings_text="skip = []\nexemption = []\nother = 1\n")
        self.assert_bad([], "settings", settings_text="skip = [\n")
        self.assert_bad([], "skip", settings_text='skip = "ruff"\nexemption = []\n')

    def test_entry_names_its_index_and_key(self):
        out = self.assert_bad(
            [entry("ruff", "src/m.py", "E501", "f"), entry("ruff", "src/b.py", "E1", "g", count=-1)], "#2"
        )
        self.assertIn('"src/b.py"', out)

    def test_s17_one_allowlist_for_four_languages(self):
        found = {
            "rust": [occurrence("rustc", "src/a.rs", "dead_code", "f")],
            "python": [occurrence("ruff", "src/m.py", "E501", "")],
            "typescript": [occurrence("tsc", "src/a.ts", "ts-ignore", "g")],
            "go": [occurrence("golangci-lint", "pkg/a.go", "errcheck", "T.Run")],
        }
        repo = Repo(
            self,
            {"src/a.rs": "", "src/m.py": "", "src/a.ts": "", "pkg/a.go": ""},
            exemptions=[entry(**r) for records in found.values() for r in records],
        )
        code, out = run(repo, FakeScan(found))
        self.assertEqual(code, 0, out)


class Reconcile(unittest.TestCase):
    def test_s18_missing_entry_is_pasteable(self):
        repo = Repo(self, {"src/a.rs": ""})
        code, out = run(repo, FakeScan({"rust": [occurrence("rustc", "src/a.rs", "dead_code", "f")]}))
        self.assertEqual(code, 1)
        block = out[out.index(ADD_HEADER) :].split("\n\n")[0]
        parsed = tomllib.loads(block)
        self.assertEqual(
            parsed["ci-checks/inline-exemptions/settings.toml"]["exemption"],
            [entry("rustc", "src/a.rs", "dead_code", "f", count=1, reason="")],
        )

    def test_s19_and_s15_count_mismatch(self):
        repo = Repo(self, {"src/m.py": ""}, exemptions=[entry("ruff", "src/m.py", "E501", "f", count=2)])
        records = [occurrence("ruff", "src/m.py", "E501", "f")] * 3
        code, out = run(repo, FakeScan({"python": records}))
        self.assertEqual(code, 1)
        self.assertIn("# count: 2 in the allowlist, 3 in the code", out)
        self.assertIn("count = 3", out)

    def test_s20_stale_entry_must_be_removed(self):
        repo = Repo(self, {"tests/test_x.py": ""}, exemptions=[entry("pytest", "tests/test_x.py", "skip", "test_y")])
        code, out = run(repo, FakeScan())
        self.assertEqual(code, 1)
        self.assertIn("# remove", out)
        self.assertIn('function = "test_y"', out)

    def test_s21_sorted_by_file_function_tool_rule(self):
        repo = Repo(self, {"src/a.py": "", "src/b.py": ""})
        records = [
            occurrence("ruff", "src/b.py", "E1", ""),
            occurrence("ruff", "src/a.py", "E2", "g"),
            occurrence("pyright", "src/a.py", "*", "g"),
            occurrence("ruff", "src/a.py", "E1", "g"),
            occurrence("ruff", "src/a.py", "E9", ""),
        ]
        _, out = run(repo, FakeScan({"python": records}))
        order = [line for line in out.splitlines() if line.startswith(("file =", "function =", "tool =", "rule ="))]
        keys = [tuple(order[i : i + 4]) for i in range(0, len(order), 4)]
        self.assertEqual(
            keys,
            [
                ('tool = "ruff"', 'file = "src/a.py"', 'rule = "E9"', 'function = ""'),
                ('tool = "pyright"', 'file = "src/a.py"', 'rule = "*"', 'function = "g"'),
                ('tool = "ruff"', 'file = "src/a.py"', 'rule = "E1"', 'function = "g"'),
                ('tool = "ruff"', 'file = "src/a.py"', 'rule = "E2"', 'function = "g"'),
                ('tool = "ruff"', 'file = "src/b.py"', 'rule = "E1"', 'function = ""'),
            ],
        )

    def test_s22_match_passes(self):
        repo = Repo(self, {"src/m.py": ""}, exemptions=[entry("ruff", "src/m.py", "E501", "f", count=2)])
        records = [occurrence("ruff", "src/m.py", "E501", "f")] * 2
        code, _ = run(repo, FakeScan({"python": records}))
        self.assertEqual(code, 0)

    def test_s32_no_exemption_section(self):
        repo = Repo(self, {"src/m.py": ""}, settings_text="skip = []\n")
        code, out = run(repo, FakeScan({"python": [occurrence("ruff", "src/m.py", "E501", "")]}))
        self.assertEqual(code, 1)
        self.assertIn(ADD_HEADER, out)


class Errors(unittest.TestCase):
    def test_s23_scanner_error_record_names_the_file(self):
        repo = Repo(self, {"src/x.py": "def (:\n"})
        code, out = run(repo, FakeScan({"python": [{"file": "src/x.py", "error": "line 1: invalid syntax"}]}))
        self.assertEqual(code, 2)
        self.assertIn('"src/x.py"', out)

    def test_scanner_crash(self):
        repo = Repo(self, {"src/a.rs": ""})
        code, out = run(repo, FakeScan(crash="rust"))
        self.assertEqual(code, 2)
        self.assertIn("rust scanner", out)

    def test_malformed_scanner_output(self):
        repo = Repo(self, {"src/a.rs": ""})
        for bad in ("not json\n", '{"tool": "nosec", "file": "a", "rule": "x", "function": ""}\n', "[1]\n"):
            code, _ = run(repo, lambda language, root, files, bad=bad: bad)
            self.assertEqual(code, 2, bad)

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.toml"
            settings.write_text("skip = []\nexemption = []\n")
            out = io.StringIO()
            self.assertEqual(ie.check(Path(tmp), settings, FakeScan(), out), 2)

    def test_nfr4_no_line_starts_a_workflow_command(self):
        repo = Repo(self, {"src/m.py": ""})
        records = [
            occurrence("ruff", "::error::x\n::warning::y", "::E1\n::x", "::f"),
            {"file": "\n::error file=a::b", "error": "\n::warning::c"},
        ]
        _, out = run(repo, FakeScan({"python": records}))
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)
        _, out = run(repo, FakeScan({"python": records[:1]}))
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)


class EndToEnd(unittest.TestCase):
    def test_python_scanner_command_shape(self):
        argv, cwd, env = ie.command("python", Path("/r"))
        self.assertEqual(argv[1:], [str(ie.CI_CHECKS / "python/inline-exemptions/exemptions.py"), "/r"])
        self.assertEqual(cwd, ie.CI_CHECKS / "python/inline-exemptions")
        argv, cwd, env = ie.command("go", Path("/r"))
        self.assertEqual((argv, env["GOWORK"]), (["go", "run", ".", "/r"], "off"))
        argv, cwd, _ = ie.command("rust", Path("/r"))
        self.assertEqual(cwd, ie.CI_CHECKS / "rust/inline-exemptions")
        self.assertIn("--locked", argv)

    def test_missing_toolchain_is_an_error(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with self.assertRaisesRegex(ie.Error, "go"):
            ie.run_scanner("go", Path("/r"), ["a.go"], runner=missing)


class Workflow(unittest.TestCase):
    REPO = ie.CI_CHECKS.parent
    TEXT = (REPO / ".github/workflows/inline-exemptions.yml").read_text()

    def test_s35_only_contents_read(self):
        self.assertIn("\npermissions:\n  contents: read\n\njobs:", self.TEXT)
        self.assertEqual(self.TEXT.count("permissions:"), 1)

    def test_s1_template_calls_it_and_the_check_is_named(self):
        template = (ie.CI_CHECKS / "ci.yml").read_text()
        self.assertIn("uses: windroc-labs/repo-guards/.github/workflows/inline-exemptions.yml@main", template)
        self.assertIn("    name: Inline exemptions\n", self.TEXT)

    def test_s4_fr1_called_only_with_runs_on_and_no_event_filter(self):
        on = self.TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertTrue(on.startswith("  workflow_call:\n    inputs:\n      runs-on:\n"))
        self.assertEqual(on.count("type:"), 1)
        self.assertNotIn("github.event_name", self.TEXT)
        self.assertNotIn("fetch-depth", self.TEXT)

    def test_fr5_overrides_apply_before_the_check(self):
        apply = self.TEXT.index("name: Apply caller overrides")
        self.assertLess(apply, self.TEXT.index("name: Find languages"))
        self.assertLess(apply, self.TEXT.index("name: Inline exemptions\n        run:"))


if __name__ == "__main__":
    unittest.main()
