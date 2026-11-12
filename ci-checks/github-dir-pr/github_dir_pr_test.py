import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import github_dir_pr

OVERRIDES = ".github/checks/repo-guards.toml"
BASE_TOML = """\
["ci-checks/.jscpd.json"]
minLines = 9

["ci-checks/inline-exemptions/settings.toml"]
skip = []

[["ci-checks/inline-exemptions/settings.toml".exemption]]
tool = "ruff"
file = "src/m.py"
rule = "E501"
function = ""
count = 1
reason = "long URL"
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


class Pr(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "ci@example.com")
        git(self.root, "config", "user.name", "ci")

    def write(self, files):
        for path, text in files.items():
            target = self.root / path
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "change")

    def pr(self, base_files, head_files):
        self.write(base_files)
        git(self.root, "checkout", "-q", "-b", "pr")
        self.write(head_files)
        out = io.StringIO()
        with redirect_stdout(out):
            code = github_dir_pr.check(self.root, "main")
        return code, out.getvalue()

    def test_only_code(self):
        self.assertEqual(self.pr({"a.py": "x\n"}, {"a.py": "y\n"})[0], 0)

    def test_only_github(self):
        self.assertEqual(self.pr({"a.py": "x\n"}, {".github/workflows/ci.yml": "on: push\n"})[0], 0)

    def test_github_and_code_fails(self):
        code, out = self.pr({"a.py": "x\n"}, {".github/workflows/ci.yml": "on: push\n", "a.py": "y\n"})
        self.assertEqual(code, 1)
        self.assertIn("::error::A PR that changes .github/ must change nothing else", out)
        self.assertIn("  .github/workflows/ci.yml", out)
        self.assertIn("  a.py", out)

    def test_allowlist_only_change_with_code_passes(self):
        head = BASE_TOML.replace('count = 1', 'count = 2')
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: head, "a.py": "y\n"})[0], 0)

    def test_allowlist_entry_added_with_code_passes(self):
        head = BASE_TOML + BASE_TOML[BASE_TOML.index("[[") :].replace("src/m.py", "src/n.py")
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: head, "a.py": "y\n"})[0], 0)

    def test_new_overrides_file_with_only_the_allowlist_passes(self):
        head = BASE_TOML[BASE_TOML.index("[[") :]
        self.assertEqual(self.pr({"a.py": "x\n"}, {OVERRIDES: head, "a.py": "y\n"})[0], 0)

    def test_skip_change_in_the_same_section_fails(self):
        head = BASE_TOML.replace("skip = []", 'skip = ["ruff"]')
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: head, "a.py": "y\n"})[0], 1)

    def test_other_section_change_fails(self):
        head = BASE_TOML.replace("minLines = 9", "minLines = 90")
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: head, "a.py": "y\n"})[0], 1)

    def test_another_github_file_beside_the_allowlist_fails(self):
        head = BASE_TOML.replace('count = 1', 'count = 2')
        code, _ = self.pr(
            {OVERRIDES: BASE_TOML, "a.py": "x\n"},
            {OVERRIDES: head, ".github/CODEOWNERS": "* @o\n", "a.py": "y\n"},
        )
        self.assertEqual(code, 1)

    def test_unparsable_head_fails(self):
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: "[[", "a.py": "y\n"})[0], 1)

    def test_deleting_overrides_with_code_fails(self):
        self.assertEqual(self.pr({OVERRIDES: BASE_TOML, "a.py": "x\n"}, {OVERRIDES: None, "a.py": "y\n"})[0], 1)


if __name__ == "__main__":
    unittest.main()
