import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from language_gate import PATTERNS, PRETTIER, main, prettier_files, touches

CI_CHECKS = Path(__file__).resolve().parent.parent
INLINE_EXEMPTIONS = CI_CHECKS / "inline-exemptions/inline_exemptions.py"

MATCHES = {
    "python": ["a.py", "src/b.pyi", "x/a.ipynb", "pyproject.toml", "sub/uv.lock", "dev-requirements.txt"]
    + ["pytest.toml", "requirements.in"],
    "rust": ["src/main.rs", "Cargo.toml", "crates/a/Cargo.lock", ".cargo/config.toml", "a/clippy.toml"],
    "typescript": ["web/a.tsx", "b.mjs", "package.json", "tsconfig.build.json", ".npmrc", "docs/guide.md", "a.scss"]
    + [".prettierrc", "web/.eslintrc", "README", "public/site.webmanifest", ".prettierignore"],
    "go": ["main.go", "go.mod", "sub/go.sum", "pkg/testdata/in.txt", "x/sum_amd64.s", "cgo/a.c", "vendor/modules.txt"],
}
SHARED = [".github/workflows/ci.yml", ".github/checks/repo-guards.toml", ".github/checks/jscpd-baseline.json"]
NEITHER = ["Makefile", "LICENSE", "a.pyc", "image.png", "notes.txt", "x/.github/checks/a.toml"]

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class Touches(unittest.TestCase):
    def test_each_language_matches_its_own_files(self):
        for language, paths in MATCHES.items():
            for path in paths:
                with self.subTest(language=language, path=path):
                    self.assertEqual(touches(language, [path]), [path])

    def test_every_glob_matches_a_file_of_its_shape(self):
        for language, globs in PATTERNS.items():
            for glob in globs:
                sample = glob.replace("*", "x")
                with self.subTest(language=language, glob=glob):
                    self.assertEqual(touches(language, [sample, f"sub/{sample}"]), [sample, f"sub/{sample}"])

    def test_no_language_matches_another_languages_files(self):
        for language in PATTERNS:
            for other, paths in MATCHES.items():
                if other != language:
                    with self.subTest(language=language, other=other):
                        self.assertEqual(touches(language, paths), [])

    def test_repo_guards_call_and_settings_match_every_language(self):
        for language in PATTERNS:
            with self.subTest(language=language):
                self.assertEqual(touches(language, SHARED), SHARED)

    def test_other_files_and_nested_github_dirs_match_no_language(self):
        for language in PATTERNS:
            with self.subTest(language=language):
                self.assertEqual(touches(language, NEITHER), [])

    def test_source_suffixes_cover_the_inline_exemption_scanners(self):
        spec = importlib.util.spec_from_file_location("inline_exemptions", INLINE_EXEMPTIONS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for language, scanned in module.LANGUAGES.items():
            for name in [f"a{suffix}" for suffix in scanned.suffixes] + list(scanned.configs):
                with self.subTest(language=language, name=name):
                    self.assertEqual(touches(language, [f"sub/{name}"]), [f"sub/{name}"])


class Prettier(unittest.TestCase):
    def test_list_is_for_the_pinned_prettier(self):
        # Bumping the pin without regenerating prettier-files.json fails here.
        pinned = json.loads((CI_CHECKS / "typescript/package.json").read_text())["devDependencies"]["prettier"]
        self.assertEqual(PRETTIER["prettier"], pinned)

    def test_prettier_files_merges_every_language(self):
        info = {"languages": [{"extensions": [".b", ".a"], "filenames": ["Z"]}, {"extensions": [".a"]}, {}]}
        expected = {"prettier": "1.0.0", "extensions": [".a", ".b"], "filenames": ["Z"]}
        self.assertEqual(prettier_files("1.0.0", info), expected)

    def test_cli_writes_the_list_from_support_info_on_stdin(self):
        out = io.StringIO()
        info = '{"languages": [{"extensions": [".md"], "filenames": ["README"]}]}'
        with mock.patch("sys.stdin", io.StringIO(info)), contextlib.redirect_stdout(out):
            self.assertEqual(main(["prettier-files", "3.0.0"]), 0)
        expected = {"prettier": "3.0.0", "extensions": [".md"], "filenames": ["README"]}
        self.assertEqual(json.loads(out.getvalue()), expected)


class Cli(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        for path in ["app.py", "src/lib.rs", "README.md"]:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("x\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "base")

    def commit(self) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "change")

    def run_gate(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(list(args))
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def test_rust_only_change_skips_python_and_runs_rust(self):
        (self.root / "src/lib.rs").write_text("y\n")
        self.commit()
        self.assertEqual(self.run_gate("python", "HEAD^")[:2], (0, "run=false\n"))
        code, out, err = self.run_gate("rust", "HEAD^")
        self.assertEqual((code, out), (0, "run=true\n"))
        self.assertIn("src/lib.rs", err)

    def test_merge_ref_against_its_first_parent_is_only_the_prs_change(self):
        # GitHub checks out a PR as a merge of its head into the base tip; the base moved on in Python meanwhile.
        git(self.root, "checkout", "-q", "-b", "pr")
        (self.root / "src/lib.rs").write_text("y\n")
        self.commit()
        git(self.root, "checkout", "-q", "main")
        (self.root / "app.py").write_text("y\n")
        self.commit()
        git(self.root, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        self.assertEqual(self.run_gate("python", "HEAD^1")[:2], (0, "run=false\n"))
        self.assertEqual(self.run_gate("rust", "HEAD^1")[:2], (0, "run=true\n"))

    def test_deleted_file_counts(self):
        (self.root / "app.py").unlink()
        self.commit()
        self.assertEqual(self.run_gate("python", "HEAD^")[:2], (0, "run=true\n"))

    def test_rename_away_from_the_language_counts(self):
        git(self.root, "mv", "app.py", "app.txt")
        self.commit()
        self.assertEqual(self.run_gate("python", "HEAD^")[:2], (0, "run=true\n"))

    def test_markdown_only_change_runs_only_typescript(self):
        # typescript.yml's Format runs prettier on the whole tree, and prettier formats Markdown.
        (self.root / "README.md").write_text("y\n")
        self.commit()
        for language in PATTERNS:
            with self.subTest(language=language):
                expected = "run=true\n" if language == "typescript" else "run=false\n"
                self.assertEqual(self.run_gate(language, "HEAD^")[:2], (0, expected))

    def test_log_counts_every_hit_and_lists_the_first_ten(self):
        for i in range(12):
            (self.root / f"m{i:02}.py").write_text("x\n")
        self.commit()
        _, _, err = self.run_gate("python", "HEAD^")
        self.assertIn("12 changed file(s)", err)
        self.assertIn('"m09.py"', err)
        self.assertNotIn('"m10.py"', err)

    def test_file_names_are_logged_quoted(self):
        (self.root / "a\n::error::x.py").write_text("y\n")
        self.commit()
        _, _, err = self.run_gate("python", "HEAD^")
        self.assertIn('"a\\n::error::x.py"', err)
        self.assertFalse(any(line.startswith("::") for line in err.splitlines()))

    def test_unknown_language_exits_2(self):
        code, out, err = self.run_gate("cobol", "HEAD")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cobol", err)

    def test_bad_base_exits_2(self):
        code, out, _ = self.run_gate("python", "no-such-ref")
        self.assertEqual((code, out), (2, ""))

    def test_wrong_arguments_exit_2(self):
        self.assertEqual(self.run_gate("python")[0], 2)


if __name__ == "__main__":
    unittest.main()
