import contextlib
import io
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import inline_exemptions
from inline_exemptions import extractor_for, main
from syntax import SourceError

ENTRY = """
[[exemption]]
tool = "ruff"
file = "src/m.py"
rule = "E501"
function = "f"
count = 1
reason = "long URL"
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(files: dict[str, bytes | str]) -> Path:
    repo = Path(tempfile.mkdtemp())
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def fake_python(path: str, source: bytes) -> list[tuple[str, str, str]]:
    # Stands in for python_source until that module exists: one ruff E501 hit per "# noqa: E501" line in f.
    text = source.decode("utf-8")
    if "def (" in text:
        raise SourceError("syntax error at line 1")
    return [("ruff", "E501", "f")] * text.count("# noqa: E501")


def run(repo: Path, settings: str = "") -> tuple[int, str]:
    path = Path(tempfile.mkdtemp()) / "settings.toml"
    path.write_text(settings, encoding="utf-8")
    module = types.SimpleNamespace(extract=fake_python)
    out = io.StringIO()
    with mock.patch.dict(inline_exemptions.MODULES, {"python_source": module}), contextlib.redirect_stdout(out):
        code = main(["--repo", str(repo), "--settings", str(path)])
    return code, out.getvalue()


class ExtractorForTest(unittest.TestCase):
    def test_fr29_files(self) -> None:
        cases = {
            "a/b.rs": "rust_source", "x.py": "python_source", "stubs/x.pyi": "python_source",
            "types/y.d.ts": "typescript_source", "a.tsx": "typescript_source", "a.cjs": "typescript_source",
            "pkg/a.go": "go_source", "crates/x/Cargo.toml": "config_files", "pyproject.toml": "config_files",
            "tools/go.mod": "config_files",
        }
        for path, module in cases.items():
            with self.subTest(path):
                self.assertEqual(extractor_for(path), module)
        for path in ["docs/a.md", "assets/logo.png", ".github/checks/repo-guards.toml", "a.css", "Cargo.lock"]:
            with self.subTest(path):
                self.assertIsNone(extractor_for(path))


class MainTest(unittest.TestCase):
    def test_s22_consistent(self) -> None:
        repo = make_repo({"src/m.py": "def f():\n    x = 1  # noqa: E501\n"})
        self.assertEqual(run(repo, ENTRY), (0, "inline exemptions: 1 found, all registered\n"))

    def test_s32_no_registration(self) -> None:
        code, output = run(make_repo({"src/m.py": "def f():\n    x = 1  # noqa: E501\n"}))
        self.assertEqual(code, 1)
        self.assertIn('# add: fill in reason\n[["ci-checks/inline-exemptions/settings.toml".exemption]]', output)
        self.assertIn('file = "src/m.py"', output)

    def test_s2_unchanged_files_checked(self) -> None:
        repo = make_repo({"src/old.py": "def f():\n    x = 1  # noqa: E501\n"})
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        git(repo, "add", "README.md")
        git(repo, "commit", "-q", "-m", "readme only")
        code, output = run(repo)
        self.assertEqual(code, 1)
        self.assertIn('file = "src/old.py"', output)

    def test_s3_submodule_not_scanned(self) -> None:
        lib = make_repo({"x.py": "def f():\n    x = 1  # noqa: E501\n"})
        repo = make_repo({"README.md": "x\n"})
        git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib")
        git(repo, "commit", "-q", "-m", "submodule")
        code, output = run(repo)
        self.assertEqual(code, 0, output)
        self.assertNotIn("vendor/lib", output)

    def test_s30_only_listed_files_read(self) -> None:
        repo = make_repo({"assets/logo.png": b"\x89PNG\xff\xfe", "docs/a.md": "<!-- prettier-ignore -->\n"})
        self.assertEqual(run(repo), (0, "inline exemptions: 0 found, all registered\n"))

    def test_s49_symlink(self) -> None:
        repo = make_repo({"assets/util.txt": "x\n"})
        (repo / "src").mkdir()
        os.symlink("../assets/util.txt", repo / "src" / "util.py")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "link")
        code, output = run(repo)
        self.assertEqual(code, 2)
        self.assertIn('"src/util.py"', output)

    def test_s23_every_bad_file_named(self) -> None:
        repo = make_repo({"src/x.py": "def (:\n", "src/y.py": b"\xff\xfe = 1\n", "src/z.py": "def f():\n    pass\n"})
        code, output = run(repo)
        self.assertEqual(code, 2)
        self.assertIn('error: "src/x.py": syntax error at line 1', output)
        self.assertIn('error: "src/y.py": not UTF-8', output)

    def test_s16_bad_allowlist_exits_2(self) -> None:
        code, output = run(make_repo({"README.md": "x\n"}), ENTRY.replace("count = 1", "count = 0"))
        self.assertEqual(code, 2)
        self.assertIn("exemption 1", output)

    def test_s33_uncommitted_changes_noted_first(self) -> None:
        repo = make_repo({"src/m.py": "def f():\n    pass\n"})
        (repo / "src/m.py").write_text("def f():\n    x = 1  # noqa: E501\n", encoding="utf-8")
        code, output = run(repo)
        self.assertEqual(code, 1)
        self.assertTrue(output.startswith("note: the work tree has uncommitted changes"), output)
        self.assertIn('file = "src/m.py"', output)

    def test_clean_tree_has_no_note(self) -> None:
        self.assertNotIn("note:", run(make_repo({"src/m.py": "x = 1\n"}))[1])

    def test_nfr4_error_lines_escaped(self) -> None:
        repo = make_repo({"::error::x.py": b"\xff\n"})
        code, output = run(repo)
        self.assertEqual(code, 2)
        for line in output.splitlines():
            self.assertFalse(line.startswith("::"), line)


if __name__ == "__main__":
    unittest.main()
