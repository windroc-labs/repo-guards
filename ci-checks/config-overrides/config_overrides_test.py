import datetime
import io
import json
import math
import os
import stat
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from config_overrides import checkout_mode, dump_toml, main, snapshot_path_error, snapshot_settings

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

    def test_project_snapshot_setting_is_not_a_file(self) -> None:
        tree = Tree(self)
        tree.root.mkdir()
        tree.override('[project."."]\napi-snapshot.rust = ".github/checks/api-rust.json"\n')
        code, out, err = tree.apply()
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(list(tree.root.iterdir()), [])

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


    def test_bad_project_table_exits_2(self) -> None:
        for text in ('project = "x"\n', '[project]\n"." = 1\n'):
            with self.subTest(text=text):
                code, out, err = run("source-dirs", self.overrides(text), ".")
                self.assertEqual((code, out), (2, ""))
                self.assertIn("project", err)


class Repo:
    """A caller checkout: ROOT with .github/checks/repo-guards.toml."""

    def __init__(self, test: unittest.TestCase, overrides: str | None = None) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        if overrides is not None:
            self.write(".github/checks/repo-guards.toml", overrides)

    def write(self, rel: str, text: str = "x\n") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def snapshot_path(self, working_directory: str, language: str) -> tuple[int, str, str]:
        return run("snapshot-path", str(self.root), working_directory, language)

    def snapshot_scope(self, working_directory: str, language: str) -> tuple[int, str, str]:
        return run("snapshot-scope", str(self.root), working_directory, language)


def setting(language: str, path: str, working_directory: str = ".") -> str:
    return f"[project.{json.dumps(working_directory)}]\napi-snapshot.{language} = {json.dumps(path)}\n"


def scope(language: str, prefixes: object, working_directory: str = ".") -> str:
    return f"[project.{json.dumps(working_directory)}]\napi-snapshot-scope.{language} = {json.dumps(prefixes)}\n"


class SnapshotScopeTest(unittest.TestCase):
    VALID = {
        "rust": ["kvm_op::driver", "kvm_op", "crate_b::a::b"],
        "python": ["pkg.sub", "pkg", "_pkg.a1"],
        "go": ["example.com/kvm/driver", "golang.org/x/net/http2", "a/b-c/v2", "mod~1/x+y"],
        "typescript": ["./driver", ".", "./a/b", "./x.js"],
    }
    INVALID = {
        "rust": ["kvm-op::driver", "kvm_op::", "::driver", "kvm_op:driver", "kvm_op::driver::*", "kvm_op.driver", ""],
        "python": ["pkg/sub", "pkg.", ".pkg", "pkg..sub", "pkg-sub", "1pkg", ""],
        "go": ["/abs/pkg", "a//b", "a/./b", "a/../b", "a/", "a b", "a/*", ""],
        "typescript": ["driver", "./", "./driver/", "./a//b", "./../x", "./a/./b", "./*", "./a/*.js", "/driver", ""],
    }

    def test_unset_prints_nothing(self) -> None:
        cases = {
            "no file": None,
            "no section": '[project."other"]\nsource-dirs = ["src"]\n',
            "path only": setting("go", ".github/api/go.json"),
            "other language only": scope("rust", ["kvm_op::driver"]),
            "other working directory": scope("go", ["a/b"], "sub"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertEqual(Repo(self, text).snapshot_scope(".", "go"), (0, "", ""))

    def test_valid_prefixes_print_one_json_line_in_order(self) -> None:
        for language, prefixes in self.VALID.items():
            with self.subTest(language):
                code, out, err = Repo(self, scope(language, prefixes)).snapshot_scope(".", language)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out.count("\n"), 1)
                self.assertEqual(json.loads(out), prefixes)

    def test_non_ascii_prefix_is_printed_verbatim(self) -> None:
        code, out, _ = Repo(self, scope("python", ["paquet.módulo"])).snapshot_scope(".", "python")
        self.assertEqual((code, out), (0, '["paquet.módulo"]\n'))

    def test_invalid_prefixes_exit_2_naming_key_and_value(self) -> None:
        for language, prefixes in self.INVALID.items():
            for prefix in prefixes:
                with self.subTest(language=language, prefix=prefix):
                    text = scope(language, [self.VALID[language][0], prefix])
                    code, out, err = Repo(self, text).snapshot_scope(".", language)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn(f'[project."."] api-snapshot-scope.{language}', err)
                    self.assertIn(json.dumps(prefix), err)

    def test_control_characters_are_rejected(self) -> None:
        for language, prefix in [("typescript", "./a\nb"), ("go", "a\x7fb"), ("rust", "a\tb"), ("python", "a\x00")]:
            with self.subTest(language=language):
                code, out, err = Repo(self, scope(language, [prefix])).snapshot_scope(".", language)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("control characters", err)

    def test_empty_list_is_rejected(self) -> None:
        code, out, err = Repo(self, scope("go", [])).snapshot_scope(".", "go")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("api-snapshot-scope.go", err)
        self.assertIn("at least one", err)

    def test_bad_setting_types_exit_2(self) -> None:
        cases = [
            '[project."."]\napi-snapshot-scope = ["a"]\n',
            '[project."."]\napi-snapshot-scope.go = "a/b"\n',
            '[project."."]\napi-snapshot-scope.go = [1]\n',
            '[project."."]\napi-snapshot-scope.go = [["a"]]\n',
            '[project."."]\napi-snapshot-scope.go = { a = "b" }\n',
        ]
        for text in cases:
            with self.subTest(text=text):
                code, out, err = Repo(self, text).snapshot_scope(".", "go")
                self.assertEqual((code, out), (2, ""))
                self.assertIn('[project."."] api-snapshot-scope', err)

    def test_unknown_language_or_bad_table_anywhere_exits_2(self) -> None:
        for text, needle in [
            (scope("rsut", ["a"], "other"), "rsut"),
            ('[project."other"]\napi-snapshot-scope = 1\n', "not a table"),
            ('project = "x"\n', "project"),
            ("[project\n", "repo-guards.toml"),
        ]:
            with self.subTest(text=text):
                code, out, err = Repo(self, text).snapshot_scope(".", "rust")
                self.assertEqual((code, out), (2, ""))
                self.assertIn(needle, err)

    def test_snapshot_path_ignores_the_scope(self) -> None:
        text = setting("go", ".github/api/go.json") + 'api-snapshot-scope.go = ["a/b"]\n'
        self.assertEqual(Repo(self, text).snapshot_path(".", "go"), (0, ".github/api/go.json\n", ""))
        bad = setting("go", ".github/api/go.json") + "api-snapshot-scope.go = []\n"
        self.assertEqual(Repo(self, bad).snapshot_path(".", "go"), (0, ".github/api/go.json\n", ""))

    def test_scope_ignores_the_snapshot_path(self) -> None:
        text = setting("go", "not/under/github.json") + 'api-snapshot-scope.go = ["a/b"]\n'
        self.assertEqual(Repo(self, text).snapshot_scope(".", "go"), (0, '["a/b"]\n', ""))

    def test_bad_arguments_exit_2(self) -> None:
        root = str(Repo(self).root)
        self.assertEqual(run("snapshot-scope", root, ".")[0], 2)
        self.assertEqual(run("snapshot-scope", root, ".", "java")[0], 2)
        self.assertEqual(run("snapshot-scope", root, ".", "go", "x")[0], 2)
        self.assertIn("snapshot-scope REPO_ROOT WORKING_DIRECTORY", run()[2])


class SnapshotPathTest(unittest.TestCase):
    def test_unset_prints_nothing(self) -> None:
        cases = {
            "no file": None,
            "no project": '["ci-checks/licenses.toml"]\nexceptions = []\n',
            "no section": '[project."other"]\nsource-dirs = ["src"]\n',
            "no api-snapshot": '[project."."]\nsource-dirs = ["src"]\n',
            "other language only": setting("rust", ".github/checks/api-rust.json"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertEqual(Repo(self, text).snapshot_path(".", "go"), (0, "", ""))

    def test_each_language_reads_its_own_path(self) -> None:
        repo = Repo(
            self,
            '[project."."]\napi-snapshot.python = ".github/api/py.json"\n'
            'api-snapshot.typescript = ".github/api/ts.json"\n',
        )
        self.assertEqual(repo.snapshot_path(".", "python"), (0, ".github/api/py.json\n", ""))
        self.assertEqual(repo.snapshot_path(".", "typescript"), (0, ".github/api/ts.json\n", ""))

    def test_other_working_directory_is_ignored(self) -> None:
        repo = Repo(self, setting("go", ".github/api/a.json", "a") + setting("go", ".github/api/b.json", "b"))
        self.assertEqual(repo.snapshot_path("b", "go"), (0, ".github/api/b.json\n", ""))
        self.assertEqual(repo.snapshot_path(".", "go"), (0, "", ""))

    def test_missing_file_is_accepted(self) -> None:
        repo = Repo(self, setting("rust", ".github/checks/api-rust.json"))
        self.assertEqual(repo.snapshot_path(".", "rust"), (0, ".github/checks/api-rust.json\n", ""))

    def test_existing_regular_file_is_accepted(self) -> None:
        repo = Repo(self, setting("go", ".github/api/go.json"))
        repo.write(".github/api/go.json", "[]\n")
        self.assertEqual(repo.snapshot_path(".", "go"), (0, ".github/api/go.json\n", ""))

    def test_rejected_paths_exit_2_naming_the_path(self) -> None:
        paths = [
            "api/go.json",
            ".github/../api/go.json",
            ".github/workflows/ci.yml",
            ".github/checks/repo-guards.toml",
            "/abs/.github/go.json",
            ".github//go.json",
            ".github/./workflows/ci.yml",
            ".github",
            ".github/",
            ".github/x\npath=.github/workflows/ci.yml",
            ".github/a\rb.json",
            ".github/a\x00b.json",
            ".github/a\x7fb.json",
        ]
        for path in paths:
            with self.subTest(path=path):
                code, out, err = Repo(self, setting("go", path)).snapshot_path(".", "go")
                self.assertEqual((code, out), (2, ""))
                self.assertIn(json.dumps(path, ensure_ascii=False), err)

    def test_s25_paths_appear_verbatim(self) -> None:
        s25 = ["api/go.json", ".github/../api/go.json", ".github/workflows/ci.yml", ".github/checks/repo-guards.toml"]
        for path in s25:
            with self.subTest(path=path):
                code, _, err = Repo(self, setting("go", path)).snapshot_path(".", "go")
                self.assertEqual(code, 2)
                self.assertIn(path, err)

    def test_symlinked_file_or_parent_is_rejected(self) -> None:
        repo = Repo(self, setting("go", ".github/api/go.json"))
        target = repo.write("elsewhere/go.json", "[]\n")
        (repo.root / ".github/api").mkdir(parents=True)
        os.symlink(target, repo.root / ".github/api/go.json")
        code, _, err = repo.snapshot_path(".", "go")
        self.assertEqual(code, 2)
        self.assertIn("symbolic link", err)

        repo = Repo(self, setting("go", ".github/api/go.json"))
        repo.write("elsewhere/go.json", "[]\n")
        os.symlink(repo.root / "elsewhere", repo.root / ".github/api")
        code, _, err = repo.snapshot_path(".", "go")
        self.assertEqual(code, 2)
        self.assertIn('".github/api" is a symbolic link', err)

    def test_directory_is_rejected(self) -> None:
        repo = Repo(self, setting("go", ".github/api"))
        (repo.root / ".github/api").mkdir()
        code, _, err = repo.snapshot_path(".", "go")
        self.assertEqual(code, 2)
        self.assertIn("not a regular file", err)

    def test_bad_setting_types_exit_2(self) -> None:
        cases = [
            'project = "x"\n',
            '[project]\n"." = 1\n',
            '[project."."]\napi-snapshot = ".github/a.json"\n',
            '[project."."]\napi-snapshot.go = 1\n',
            '[project."."]\napi-snapshot.go = [".github/a.json"]\n',
        ]
        for text in cases:
            with self.subTest(text=text):
                code, out, err = Repo(self, text).snapshot_path(".", "go")
                self.assertEqual((code, out), (2, ""))
                self.assertIn("project", err)

    def test_unknown_language_key_exits_2(self) -> None:
        code, out, err = Repo(self, setting("rsut", ".github/a.json")).snapshot_path(".", "rust")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("rsut", err)

    def test_invalid_toml_exits_2(self) -> None:
        code, out, err = Repo(self, "[project\n").snapshot_path(".", "go")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("repo-guards.toml", err)

    def test_bad_arguments_exit_2(self) -> None:
        root = str(Repo(self).root)
        self.assertEqual(run("snapshot-path", root, ".")[0], 2)
        self.assertEqual(run("snapshot-path", root, ".", "java")[0], 2)
        self.assertEqual(run("snapshot-path", root, ".", "go", "x")[0], 2)


GIT_MODES = {
    "120000": stat.S_IFLNK,
    "040000": stat.S_IFDIR,
    "100644": stat.S_IFREG | 0o644,
    "100755": stat.S_IFREG | 0o755,
}


def git_tree(entries: dict[str, str]):
    """A mode lookup over `git ls-tree -r -t` style entries, as a revision would give."""
    return lambda path: GIT_MODES[entries[path]] if path in entries else None


class SnapshotPathErrorTest(unittest.TestCase):
    def test_text_rules_need_no_lookup(self) -> None:
        def never(path: str) -> int | None:
            raise AssertionError(f"looked up {path}")

        reasons = {
            ".github/a\nb": "control characters",
            "a/../.github/x": '".."',
            "/.github/x": "relative",
            "api/go.json": "under .github/",
            ".github": "under .github/",
            ".github/workflows/ci.yml": ".github/workflows/",
            ".github/checks/repo-guards.toml": "repo-guards.toml",
        }
        for path, reason in reasons.items():
            with self.subTest(path=path):
                self.assertIn(reason, snapshot_path_error(path, never) or "")

    def test_revision_modes(self) -> None:
        tree = git_tree(
            {".github": "040000", ".github/api": "120000", ".github/d": "040000", ".github/ok.json": "100644"}
        )
        self.assertIn("symbolic link", snapshot_path_error(".github/api/go.json", tree) or "")
        self.assertIn("not a regular file", snapshot_path_error(".github/d", tree) or "")
        self.assertIsNone(snapshot_path_error(".github/ok.json", tree))
        self.assertIsNone(snapshot_path_error(".github/new.json", tree))

    def test_checkout_mode(self) -> None:
        repo = Repo(self)
        repo.write(".github/f.json")
        mode = checkout_mode(repo.root)
        self.assertTrue(stat.S_ISREG(mode(".github/f.json") or 0))
        self.assertTrue(stat.S_ISDIR(mode(".github") or 0))
        self.assertIsNone(mode(".github/missing.json"))
        self.assertIsNone(mode(".github/f.json/below"))

    def test_settings_from_parsed_toml(self) -> None:
        overrides = tomllib.loads(setting("go", ".github/a.json", "x") + setting("rust", ".github/b.json"))
        self.assertEqual(
            snapshot_settings(overrides), {("x", "go"): ".github/a.json", (".", "rust"): ".github/b.json"}
        )
        self.assertEqual(snapshot_settings({}), {})


class SnapshotJobTest(unittest.TestCase):
    """Each language's API snapshot job reads the scope with the path and hands it to Generate."""

    WORKFLOWS = CI_CHECKS.parent / ".github/workflows"

    def steps(self, language: str) -> dict[str, str]:
        text = (self.WORKFLOWS / f"{language}.yml").read_text(encoding="utf-8")
        job = text.split("\n  api-snapshot:\n", 1)[1].split("\n\n  ", 1)[0]
        steps = {}
        for step in job.split("\n      - ")[1:]:
            name = next((line.strip() for line in step.splitlines() if line.strip().startswith("name: ")), step)
            steps[name.removeprefix("name: ")] = step
        return steps

    def test_read_step_outputs_the_scope(self) -> None:
        for language in ("rust", "python", "typescript", "go"):
            with self.subTest(language):
                step = self.steps(language)["Read the snapshot settings"]
                self.assertIn("id: setting", step)
                self.assertIn(
                    'scope=$(uv run --no-project --python 3.12 "$RG/config-overrides/config_overrides.py" '
                    'snapshot-scope "$GITHUB_WORKSPACE/repo" "$PROJECT_DIR" "$LANGUAGE")',
                    step,
                )
                self.assertIn('echo "scope=$scope" >> "$GITHUB_OUTPUT"', step)

    def test_generate_gets_the_scope_in_its_environment(self) -> None:
        for language in ("rust", "python", "typescript", "go"):
            with self.subTest(language):
                step = self.steps(language)["Generate"]
                self.assertIn("API_SNAPSHOT_SCOPE: ${{ steps.setting.outputs.scope }}", step)
                self.assertNotIn("Read the snapshot path", (self.WORKFLOWS / f"{language}.yml").read_text())


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
