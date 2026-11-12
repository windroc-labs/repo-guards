import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from inline_exemptions_test import make_repo

HERE = Path(__file__).resolve().parent
PWNED = Path("/tmp/pwned")

FOUR_LANGUAGES = {
    "src/lib.rs": "#[allow(dead_code)]\nfn f() {}\n",
    "src/m.py": "def g():\n    return 1  # noqa: E501\n",
    "src/a.ts": "export function h(): void {\n  // @ts-ignore\n  x();\n}\n",
    "pkg/a.go": "package a\n\nfunc K() {\n\tx() //nolint:errcheck\n}\n",
}
REGISTERED = """
[[exemption]]
tool = "rustc"
file = "src/lib.rs"
rule = "dead_code"
function = "f"
count = 1
reason = "kept for the next release"

[[exemption]]
tool = "ruff"
file = "src/m.py"
rule = "E501"
function = "g"
count = 1
reason = "URL"

[[exemption]]
tool = "tsc"
file = "src/a.ts"
rule = "ts-ignore"
function = "h"
count = 1
reason = "untyped module"

[[exemption]]
tool = "golangci-lint"
file = "pkg/a.go"
rule = "errcheck"
function = "K"
count = 1
reason = "best effort"
"""


def check(repo: Path, settings: str, script: Path = HERE / "inline_exemptions.py") -> tuple[int, str]:
    path = Path(tempfile.mkdtemp()) / "settings.toml"
    path.write_text(settings, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "--settings", str(path)], cwd=repo, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout + result.stderr


class EndToEndTest(unittest.TestCase):
    def test_s17_one_allowlist_for_four_languages(self) -> None:
        self.assertEqual(check(make_repo(FOUR_LANGUAGES), REGISTERED), (0, "inline exemptions: 4 found, all registered\n"))

    def test_s5_skipped_tool(self) -> None:
        repo = make_repo({"src/lib.rs": "#[test]\n#[ignore]\nfn t() {}\n"})
        self.assertEqual(check(repo, 'skip = ["cargo-test"]\n')[0], 0)
        self.assertEqual(check(repo, "")[0], 1)

    def test_s2_untouched_file(self) -> None:
        repo = make_repo({"src/old.rs": "fn f() {\n    #[allow(dead_code)]\n    let x = 1;\n}\n"})
        (repo / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "docs"], check=True)
        code, output = check(repo, "")
        self.assertEqual(code, 1)
        self.assertIn('file = "src/old.rs"', output)

    def test_s24_local_copy_gives_the_same_result(self) -> None:
        repo = make_repo({**FOUR_LANGUAGES, ".gitignore": ".repo-guards/\n"})
        local = repo / ".repo-guards/ci-checks/inline-exemptions"
        shutil.copytree(HERE, local, ignore=shutil.ignore_patterns("__pycache__"))
        settings = REGISTERED.replace('count = 1\nreason = "URL"', 'count = 2\nreason = "URL"')
        ci, here = check(repo, settings), check(repo, settings, local / "inline_exemptions.py")
        self.assertEqual(ci[0], 1)
        self.assertEqual(ci, here)

    def test_s34_caller_code_never_runs(self) -> None:
        self.assertFalse(PWNED.exists(), "remove /tmp/pwned before running this test")
        write = f"open('{PWNED}', 'w').write('x')"
        repo = make_repo(
            {
                "tests/conftest.py": f"{write}\n",
                "build.rs": f'fn main() {{ std::fs::write("{PWNED}", "x").unwrap(); }}\n',
                "Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\nbuild = "build.rs"\n',
                "package.json": f'{{"scripts": {{"postinstall": "touch {PWNED}"}}}}\n',
                "tools/gen.go": f'package main\n\nimport "os"\n\nfunc init() {{\n\tos.WriteFile("{PWNED}", nil, 0o600)\n}}\n',
                "go.mod": "module x\n",
            }
        )
        code, output = check(repo, "")
        self.assertEqual(code, 0, output)
        self.assertFalse(PWNED.exists())
        self.assertFalse(any(name in os.listdir(repo) for name in ("target", "node_modules")))


if __name__ == "__main__":
    unittest.main()
