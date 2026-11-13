import io
import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
import unittest.mock
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


def run(repo, scan, target):
    out = io.StringIO()
    code = ie.check(repo.root, repo.settings, scan, out, target)
    return code, out.getvalue()


class Files(unittest.TestCase):
    def test_s2_unchanged_files_are_checked(self):
        repo = Repo(self, {"src/old.rs": "#[allow(dead_code)]\nfn f() {}\n", "README.md": "a\n"})
        (repo.root / "README.md").write_text("b\n")
        git(repo.root, "commit", "-q", "-am", "readme only")
        scan = FakeScan({"rust": [occurrence("rustc", "src/old.rs", "dead_code", "f")]})
        code, out = run(repo, scan, "rust")
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
        code, out = run(repo, scan, "jscpd")
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
        code, out = run(repo, scan, "jscpd")
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
        code, out = run(repo, FakeScan(), "python")
        self.assertEqual(code, 2)
        self.assertIn('"src/util.py"', out)
        self.assertNotIn("src_util_link", out)

    def test_s33_uncommitted_changes_print_a_notice_first(self):
        repo = Repo(self, {"src/m.py": "x = 1\n"})
        (repo.root / "src/m.py").write_text("x = 2  # noqa: E501\n")
        scan = FakeScan({"python": [occurrence("ruff", "src/m.py", "E501", "")]})
        code, out = run(repo, scan, "python")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted", out.splitlines()[0])

    def test_clean_tree_has_no_notice(self):
        repo = Repo(self, {"src/m.py": "x = 1\n"})
        _, out = run(repo, FakeScan(), "python")
        self.assertNotIn("uncommitted", out)

    def test_tracked_file_deleted_from_work_tree_is_skipped(self):
        repo = Repo(self, {"src/m.py": "x = 1\n", "src/n.py": "y = 1\n"})
        (repo.root / "src/n.py").unlink()
        scan = FakeScan()
        run(repo, scan, "python")
        self.assertEqual(scan.calls, {"python": ["src/m.py"]})

    def test_languages(self):
        repo = Repo(self, {"src/m.py": "", "Cargo.toml": "[package]\n", "README.md": ""})
        self.assertEqual(
            ie.languages(repo.root), {"rust": True, "python": True, "go": False, "typescript": False}
        )


class Skip(unittest.TestCase):
    def test_s5_skipped_tool_is_not_checked(self):
        repo = Repo(self, {"src/lib.rs": "#[ignore]\nfn t() {}\n"}, skip=["cargo-test"])
        code, out = run(repo, FakeScan({"rust": [occurrence("cargo-test", "src/lib.rs", "ignore", "t")]}), "rust")
        self.assertEqual(code, 0, out)

    def test_s45_unknown_tool_in_skip_fails(self):
        repo = Repo(self, {"src/m.py": ""}, skip=["nosec"])
        for target in ie.TARGETS:
            code, out = run(repo, FakeScan(), target)
            self.assertEqual(code, 2, target)
            self.assertIn('"nosec"', out)

    def test_s46_entry_of_a_skipped_tool_must_be_removed(self):
        repo = Repo(
            self,
            {"src/lib.rs": "#[ignore]\nfn t() {}\n"},
            skip=["cargo-test"],
            exemptions=[entry("cargo-test", "src/lib.rs", "ignore", "t")],
        )
        scan = FakeScan({"rust": [occurrence("cargo-test", "src/lib.rs", "ignore", "t")]})
        code, out = run(repo, scan, "rust")
        self.assertEqual(code, 1)
        self.assertIn("# remove", out)
        self.assertIn('tool = "cargo-test"', out)
        self.assertEqual(run(repo, scan, "python")[0], 0)


class Allowlist(unittest.TestCase):
    def assert_bad(self, exemptions, needle, settings_text=None):
        repo = Repo(self, {"src/m.py": ""}, exemptions=exemptions, settings_text=settings_text)
        for target in ie.TARGETS:
            code, out = run(repo, FakeScan(), target)
            self.assertEqual(code, 2, (target, out))
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
        for target in ie.TARGETS:
            code, out = run(repo, FakeScan(found), target)
            self.assertEqual(code, 0, (target, out))


class Reconcile(unittest.TestCase):
    def test_s18_missing_entry_is_pasteable(self):
        repo = Repo(self, {"src/a.rs": ""})
        code, out = run(repo, FakeScan({"rust": [occurrence("rustc", "src/a.rs", "dead_code", "f")]}), "rust")
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
        code, out = run(repo, FakeScan({"python": records}), "python")
        self.assertEqual(code, 1)
        self.assertIn("# count: 2 in the allowlist, 3 in the code", out)
        self.assertIn("count = 3", out)

    def test_s20_stale_entry_must_be_removed(self):
        repo = Repo(self, {"tests/test_x.py": ""}, exemptions=[entry("pytest", "tests/test_x.py", "skip", "test_y")])
        code, out = run(repo, FakeScan(), "python")
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
        _, out = run(repo, FakeScan({"python": records}), "python")
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
        code, _ = run(repo, FakeScan({"python": records}), "python")
        self.assertEqual(code, 0)

    def test_s32_no_exemption_section(self):
        repo = Repo(self, {"src/m.py": ""}, settings_text="skip = []\n")
        code, out = run(repo, FakeScan({"python": [occurrence("ruff", "src/m.py", "E501", "")]}), "python")
        self.assertEqual(code, 1)
        self.assertIn(ADD_HEADER, out)


class Errors(unittest.TestCase):
    def test_s23_scanner_error_record_names_the_file(self):
        repo = Repo(self, {"src/x.py": "def (:\n"})
        code, out = run(repo, FakeScan({"python": [{"file": "src/x.py", "error": "line 1: invalid syntax"}]}), "python")
        self.assertEqual(code, 2)
        self.assertIn('"src/x.py"', out)

    def test_scanner_crash(self):
        repo = Repo(self, {"src/a.rs": ""})
        code, out = run(repo, FakeScan(crash="rust"), "rust")
        self.assertEqual(code, 2)
        self.assertIn("rust scanner", out)

    def test_malformed_scanner_output(self):
        repo = Repo(self, {"src/a.rs": ""})
        for bad in ("not json\n", '{"tool": "nosec", "file": "a", "rule": "x", "function": ""}\n', "[1]\n"):
            code, _ = run(repo, lambda language, root, files, bad=bad: bad, "rust")
            self.assertEqual(code, 2, bad)

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.toml"
            settings.write_text("skip = []\nexemption = []\n")
            out = io.StringIO()
            self.assertEqual(ie.check(Path(tmp), settings, FakeScan(), out, "python"), 2)

    def test_nfr4_no_line_starts_a_workflow_command(self):
        repo = Repo(self, {"src/m.py": ""})
        records = [
            occurrence("ruff", "::error::x\n::warning::y", "::E1\n::x", "::f"),
            {"file": "\n::error file=a::b", "error": "\n::warning::c"},
        ]
        _, out = run(repo, FakeScan({"python": records}), "python")
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)
        _, out = run(repo, FakeScan({"python": records[:1]}), "python")
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("::"), line)


class Targets(unittest.TestCase):
    """Each language workflow reconciles its own tools; jscpd.yml reconciles jscpd."""

    FILES = {"src/a.rs": "", "src/m.py": "", "src/a.ts": "", "pkg/a.go": "", "README.md": ""}

    def test_language_target_scans_only_its_language(self):
        repo = Repo(self, self.FILES)
        scan = FakeScan({"python": [occurrence("ruff", "src/m.py", "E501", "")]})
        code, out = run(repo, scan, "rust")
        self.assertEqual(code, 0, out)
        self.assertEqual(scan.calls, {"rust": ["src/a.rs"]})

    def test_language_target_ignores_other_tools_entries(self):
        repo = Repo(self, self.FILES, exemptions=[entry("ruff", "src/m.py", "E501", "f")])
        code, out = run(repo, FakeScan({"rust": [occurrence("clippy", "src/a.rs", "clippy::panic", "f")]}), "rust")
        self.assertEqual(code, 1)
        self.assertIn('tool = "clippy"', out)
        self.assertNotIn("ruff", out)
        self.assertIn("1 to add, 0 to remove, 0 to change count.", out)

    def test_language_target_drops_jscpd_keys_its_scanner_reports(self):
        repo = Repo(self, self.FILES)
        code, out = run(repo, FakeScan({"rust": [occurrence("jscpd", "src/a.rs", "ignore", "f")]}), "rust")
        self.assertEqual(code, 0, out)

    def test_generated_belongs_to_its_files_language(self):
        repo = Repo(self, self.FILES)
        records = {"rust": [occurrence("generated", "src/a.rs", "generated", "")]}
        code, out = run(repo, FakeScan(records), "rust")
        self.assertEqual(code, 1)
        self.assertIn('tool = "generated"', out)
        self.assertEqual(run(repo, FakeScan(records), "jscpd")[0], 0)
        repo = Repo(self, self.FILES, exemptions=[entry("generated", "src/a.rs", "generated", "")])
        self.assertEqual(run(repo, FakeScan(), "python")[0], 0)
        self.assertEqual(run(repo, FakeScan(), "rust")[0], 1)

    def test_generated_entry_for_a_file_of_no_language_fails_everywhere(self):
        repo = Repo(self, self.FILES, exemptions=[entry("generated", "README.md", "generated", "")])
        for target in ie.TARGETS:
            code, out = run(repo, FakeScan(), target)
            self.assertEqual(code, 2, target)
            self.assertIn('"README.md"', out)

    def test_jscpd_target_scans_every_language_and_keeps_only_jscpd(self):
        repo = Repo(self, self.FILES, exemptions=[entry("jscpd", "src/a.ts", "ignore", "g")])
        scan = FakeScan(
            {
                "rust": [occurrence("jscpd", "src/a.rs", "ignore", "f"), occurrence("clippy", "src/a.rs", "clippy::x", "f")],
                "typescript": [occurrence("jscpd", "src/a.ts", "ignore", "g")],
                "python": [occurrence("ruff", "src/m.py", "E501", "")],
            }
        )
        code, out = run(repo, scan, "jscpd")
        self.assertEqual(set(scan.calls), {"rust", "python", "typescript", "go"})
        self.assertEqual(code, 1)
        self.assertIn('file = "src/a.rs"', out)
        self.assertNotIn("clippy", out)
        self.assertNotIn("ruff", out)
        self.assertIn("1 to add, 0 to remove, 0 to change count.", out)

    def test_symlink_fails_only_where_its_language_is_scanned(self):
        repo = Repo(self, {"assets/util.txt": "x = 1\n", "src/a.rs": ""})
        (repo.root / "src/util.py").symlink_to("../assets/util.txt")
        git(repo.root, "add", "-A")
        git(repo.root, "commit", "-q", "-m", "link")
        self.assertEqual(run(repo, FakeScan(), "python")[0], 2)
        self.assertEqual(run(repo, FakeScan(), "jscpd")[0], 2)
        self.assertEqual(run(repo, FakeScan(), "rust")[0], 0)

    def test_every_tool_has_one_owner(self):
        owned = [tool for tools in ie.OWNED.values() for tool in tools]
        self.assertEqual(len(owned), len(set(owned)))
        self.assertEqual(ie.TOOLS, frozenset(owned) | {"generated"})
        self.assertEqual(set(ie.OWNED), set(ie.TARGETS))
        self.assertEqual(ie.owner("generated", "x/y.pyi"), "python")
        self.assertIsNone(ie.owner("generated", "README.md"))
        self.assertEqual(ie.owner("jscpd", "src/a.rs"), "jscpd")
        self.assertEqual(ie.owner("go-mod", "go.mod"), "go")


class Main(unittest.TestCase):
    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch("sys.stdout", out), unittest.mock.patch("sys.stderr", err):
            code = ie.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_target_is_required(self):
        for argv in ([], ["nope"], ["rust", "go"]):
            code, out = self.call(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("usage", out)

    def test_target_runs_check(self):
        with unittest.mock.patch.object(ie, "check", return_value=0) as check:
            self.assertEqual(self.call(["go"])[0], 0)
        self.assertEqual(check.call_args.args[4], "go")


class EndToEnd(unittest.TestCase):
    def test_python_scanner_command_shape(self):
        argv, cwd, env = ie.command("python", Path("/r"))
        self.assertEqual(argv[1:], [str(ie.CI_CHECKS / "python/inline-exemptions/exemptions.py"), "/r"])
        self.assertEqual(cwd, ie.CI_CHECKS / "python/inline-exemptions")
        argv, cwd, env = ie.command("go", Path("/r"))
        self.assertEqual((argv, env["GOWORK"]), (["go", "run", ".", "/r"], "off"))
        argv, cwd, _ = ie.command("rust", Path("/r"))
        self.assertIn("--locked", argv)
        self.assertIn(str(ie.CI_CHECKS / "rust/inline-exemptions/Cargo.toml"), argv)

    def test_rust_runs_outside_the_caller_tree_on_the_shared_toolchain(self):
        channel = tomllib.loads((ie.CI_CHECKS / "rust/rust-toolchain.toml").read_text())["toolchain"]["channel"]
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUSTUP_TOOLCHAIN", None)
            _, cwd, env = ie.command("rust", Path("/r"))
            self.assertEqual(env["RUSTUP_TOOLCHAIN"], channel)
        with unittest.mock.patch.dict(os.environ, {"RUSTUP_TOOLCHAIN": "1.2.3"}):
            self.assertEqual(ie.command("rust", Path("/r"))[2]["RUSTUP_TOOLCHAIN"], "1.2.3")
        self.assertEqual(cwd, Path("/"))
        manifest = tomllib.loads((ie.CI_CHECKS / "rust/inline-exemptions/Cargo.toml").read_text())
        self.assertIn("workspace", manifest)

    def test_missing_toolchain_is_an_error(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with self.assertRaisesRegex(ie.Error, "go"):
            ie.run_scanner("go", Path("/r"), ["a.go"], runner=missing)


class Workflows(unittest.TestCase):
    """The check is a step of each language workflow's Checks job and of jscpd.yml; callers add nothing."""

    WORKFLOWS = ie.CI_CHECKS.parent / ".github/workflows"
    GATE = "if: ${{ !cancelled() && steps.inline-exemptions.outcome == 'success' }}"
    # Languages whose Checks job runs a Layer rules step, right after Inline exemptions.
    LAYER_RULES = frozenset({"typescript"})

    @staticmethod
    def steps(job):
        return job.split("\n      - ")[1:]

    @staticmethod
    def name(step):
        # An anchored step (- &id) has its name on the next line; an alias step (- *id) is only its alias.
        if step.startswith("*"):
            return step.split("\n", 1)[0]
        return next(line.strip().removeprefix("name: ") for line in step.splitlines() if line.strip().startswith("name: "))

    def checks_job(self, language):
        text = (self.WORKFLOWS / f"{language}.yml").read_text()
        self.assertIn("\npermissions:\n  contents: read\n", text)
        # Job-level blocks (the API snapshot job declares its own) may only repeat the read-only token.
        for block in re.findall(r"^( *)permissions:\n((?:\1  .*\n)*)", text, re.M):
            self.assertEqual(block[1].split(), ["contents:", "read"], block)
        return text.split("\n  checks:\n", 1)[1].split("\n  coverage:\n", 1)[0]

    def test_no_standalone_workflow_or_template_job(self):
        self.assertFalse((self.WORKFLOWS / "inline-exemptions.yml").exists())
        self.assertNotIn("inline-exemptions", (ie.CI_CHECKS / "ci.yml").read_text())

    def test_language_step_is_the_first_check_and_gates_the_rest(self):
        for language in ie.LANGUAGES:
            with self.subTest(language):
                steps = self.steps(self.checks_job(language))
                names = [self.name(step) for step in steps]
                at = names.index("Inline exemptions")
                self.assertLess(names.index("Apply caller overrides"), at)
                self.assertEqual("Layer rules" in names, language in self.LAYER_RULES)
                if language in self.LAYER_RULES:
                    self.assertEqual(names[at + 1], "Layer rules")
                step = steps[at]
                self.assertIn("id: inline-exemptions", step)
                self.assertIn("working-directory: repo\n", step)
                self.assertIn('UV_NO_CONFIG: "1"', step)
                self.assertTrue(step.rstrip().endswith(f'inline_exemptions.py" {language}'), step)
                for later in steps[at + 1 :]:
                    self.assertIn(self.GATE, later)
                for earlier in steps[:at]:
                    self.assertNotIn("cancelled()", earlier)

    def test_jscpd_step_runs_before_duplication_and_stops_it(self):
        text = (self.WORKFLOWS / "jscpd.yml").read_text()
        self.assertIn("\npermissions:\n  contents: read\n", text)
        self.assertIn('UV_NO_CONFIG: "1"', text)
        steps = self.steps(text)
        names = [self.name(step) for step in steps]
        at = names.index("Inline exemptions")
        self.assertLess(names.index("Apply caller overrides"), names.index("Find languages"))
        self.assertLess(names.index("Find languages"), at)
        self.assertTrue(steps[at].rstrip().endswith('inline_exemptions.py" jscpd'))
        self.assertLess(at, names.index("Duplication"))
        for later in steps[at + 1 :]:
            self.assertNotIn("if:", later)


if __name__ == "__main__":
    unittest.main()
