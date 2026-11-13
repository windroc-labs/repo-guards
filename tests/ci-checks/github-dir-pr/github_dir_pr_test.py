import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "guards/ci-checks/github-dir-pr"
sys.path.insert(0, str(SOURCE))

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


class Repo(unittest.TestCase):
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


class Pr(Repo):
    def test_only_code(self):
        self.assertEqual(self.pr({"a.py": "x\n"}, {"a.py": "y\n"})[0], 0)

    def test_only_github(self):
        self.assertEqual(self.pr({"a.py": "x\n"}, {".github/workflows/ci.yml": "on: push\n"})[0], 0)

    def test_github_and_code_fails(self):
        code, out = self.pr({"a.py": "x\n"}, {".github/workflows/ci.yml": "on: push\n", "a.py": "y\n"})
        self.assertEqual(code, 1)
        self.assertIn("::error::A PR that changes .github/ must change nothing else", out)
        self.assertIn('  ".github/workflows/ci.yml"', out)
        self.assertIn('  "a.py"', out)

    def test_file_names_never_start_a_workflow_command(self):
        code, out = self.pr({"a.py": "x\n"}, {".github/x.yml": "a\n", "b\n::warning::x": "y\n"})
        self.assertEqual(code, 1)
        self.assertEqual([l for l in out.splitlines() if l.lstrip().startswith("::")], [out.splitlines()[0]])

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


RUST_SNAPSHOT = ".github/checks/api-rust.json"


def snapshot_toml(**paths):
    """BASE_TOML plus [project."."] api-snapshot paths, e.g. rust=".github/checks/api-rust.json"."""
    lines = [f'{language} = "{path}"' for language, path in paths.items()]
    return BASE_TOML + '\n[project."."."api-snapshot"]\n' + "\n".join(lines) + "\n"


class Snapshot(Repo):
    """Item 21 lets the snapshot files the merge-base configures change beside other files."""

    def base(self, toml, **extra):
        return {OVERRIDES: toml, "src/lib.rs": "pub fn a() {}\n", **extra}

    def test_s18_snapshot_and_code_pass(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        code, out = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {"src/lib.rs": "pub fn b() {}\n", RUST_SNAPSHOT: '["b"]\n'},
        )
        self.assertEqual((code, out), (0, ""))

    def test_new_snapshot_file_with_code_passes(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        code, _ = self.pr(self.base(toml), {"src/lib.rs": "pub fn b() {}\n", RUST_SNAPSHOT: "[]\n"})
        self.assertEqual(code, 0)

    def test_s19_snapshot_exemption_and_code_pass(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        code, _ = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {
                "src/lib.rs": "pub fn b() {}\n",
                RUST_SNAPSHOT: '["b"]\n',
                OVERRIDES: toml.replace("count = 1", "count = 2"),
            },
        )
        self.assertEqual(code, 0)

    def test_s20_changing_the_snapshot_path_with_code_fails(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        moved = ".github/api/rust.json"
        code, out = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {"src/lib.rs": "pub fn b() {}\n", OVERRIDES: toml.replace(RUST_SNAPSHOT, moved), moved: "[]\n"},
        )
        self.assertEqual(code, 1)
        self.assertIn(f"  {json.dumps(OVERRIDES)}", out)

    def test_s20_settings_change_alone_beside_snapshot_and_code_fails(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        code, out = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {
                "src/lib.rs": "pub fn b() {}\n",
                RUST_SNAPSHOT: '["b"]\n',
                OVERRIDES: toml.replace(RUST_SNAPSHOT, ".github/api/rust.json"),
            },
        )
        self.assertEqual(code, 1)
        self.assertIn(f"  {json.dumps(OVERRIDES)}", out)
        self.assertNotIn(json.dumps(RUST_SNAPSHOT), out)

    def test_setting_a_snapshot_path_with_code_fails(self):
        code, _ = self.pr(
            self.base(BASE_TOML),
            {"src/lib.rs": "pub fn b() {}\n", OVERRIDES: snapshot_toml(rust=RUST_SNAPSHOT), RUST_SNAPSHOT: "[]\n"},
        )
        self.assertEqual(code, 1)

    def test_s26_workflow_path_is_not_exempt(self):
        workflow = ".github/workflows/ci.yml"
        code, out = self.pr(
            self.base(snapshot_toml(rust=workflow), **{workflow: "on: push\n"}),
            {"src/lib.rs": "pub fn b() {}\n", workflow: "on: pull_request\n"},
        )
        self.assertEqual(code, 1)
        self.assertIn(f"  {json.dumps(workflow)}", out)

    def test_other_github_files_set_as_snapshots_are_not_exempt(self):
        for path in [".github/CODEOWNERS", ".github/scripts/x", ".github/checks/api-go.json"]:
            with self.subTest(path=path):
                self.setUp()
                code, out = self.pr(
                    self.base(snapshot_toml(rust=path), **{path: "a\n"}),
                    {"src/lib.rs": "pub fn b() {}\n", path: "b\n"},
                )
                self.assertEqual(code, 1)
                self.assertIn(f"  {json.dumps(path)}", out)

    def test_overrides_path_is_not_exempt(self):
        toml = snapshot_toml(rust=OVERRIDES)
        head = {"src/lib.rs": "pub fn b() {}\n", OVERRIDES: toml.replace("minLines = 9", "minLines = 90")}
        self.assertEqual(self.pr(self.base(toml), head)[0], 1)

    def test_other_working_directories_and_languages_pass(self):
        go = ".github/checks/api-go.json"
        toml = snapshot_toml(rust=RUST_SNAPSHOT) + f'\n[project."svc"."api-snapshot"]\ngo = "{go}"\n'
        code, _ = self.pr(
            self.base(toml, **{go: "[]\n"}),
            {"svc/main.go": "package main\n", go: '["Open"]\n'},
        )
        self.assertEqual(code, 0)

    def test_snapshot_beside_another_github_file_fails_and_lists_only_the_other(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        code, out = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {"src/lib.rs": "pub fn b() {}\n", RUST_SNAPSHOT: '["b"]\n', ".github/CODEOWNERS": "* @o\n"},
        )
        self.assertEqual(code, 1)
        self.assertIn('  ".github/CODEOWNERS"', out)
        self.assertNotIn(json.dumps(RUST_SNAPSHOT), out)

    def test_failure_names_the_snapshot_exception(self):
        _, out = self.pr({"a.py": "x\n"}, {".github/x.yml": "a\n", "a.py": "y\n"})
        self.assertIn('[project."<working-directory>"] api-snapshot', out)

    def test_snapshot_made_a_symlink_fails(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT)
        self.write(self.base(toml, **{RUST_SNAPSHOT: "[]\n"}))
        git(self.root, "checkout", "-q", "-b", "pr")
        (self.root / RUST_SNAPSHOT).unlink()
        (self.root / RUST_SNAPSHOT).symlink_to("../../src/lib.rs")
        self.write({"src/lib.rs": "pub fn b() {}\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(github_dir_pr.check(self.root, "main"), 1)

    def test_snapshot_under_a_symlinked_directory_is_not_exempt(self):
        self.write(self.base(snapshot_toml(rust=".github/api/rust.json"), **{"api/rust.json": "[]\n"}))
        (self.root / ".github/api").symlink_to("../api")
        self.write({})
        self.assertEqual(github_dir_pr.snapshot_files(self.root, git(self.root, "rev-parse", "HEAD").strip()), set())

    def test_snapshot_that_was_a_symlink_at_merge_base_fails(self):
        self.write(self.base(snapshot_toml(rust=RUST_SNAPSHOT), **{"api.json": "[]\n"}))
        (self.root / RUST_SNAPSHOT).symlink_to("../../api.json")
        self.write({})
        git(self.root, "checkout", "-q", "-b", "pr")
        (self.root / RUST_SNAPSHOT).unlink()
        self.write({RUST_SNAPSHOT: '["b"]\n', "src/lib.rs": "pub fn b() {}\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(github_dir_pr.check(self.root, "main"), 1)
        self.assertIn(f"  {json.dumps(RUST_SNAPSHOT)}", out.getvalue())

    def test_rejected_path_leaves_its_valid_sibling_exempt(self):
        workflow = ".github/workflows/ci.yml"
        toml = snapshot_toml(rust=RUST_SNAPSHOT, go=workflow)
        code, _ = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n", workflow: "on: push\n"}),
            {"src/lib.rs": "pub fn b() {}\n", RUST_SNAPSHOT: '["b"]\n'},
        )
        self.assertEqual(code, 0)
        merge_base = git(self.root, "rev-parse", "main").strip()
        self.assertEqual(github_dir_pr.snapshot_files(self.root, merge_base), {RUST_SNAPSHOT})

    def test_unparsable_overrides_at_merge_base_exempt_nothing(self):
        code, _ = self.pr(
            {OVERRIDES: "[[", RUST_SNAPSHOT: "[]\n", "src/lib.rs": "x\n"},
            {"src/lib.rs": "y\n", RUST_SNAPSHOT: '["b"]\n'},
        )
        self.assertEqual(code, 1)

    def test_invalid_snapshot_settings_at_merge_base_exempt_nothing(self):
        toml = snapshot_toml(rust=RUST_SNAPSHOT, cobol=".github/api/cobol.json")
        code, _ = self.pr(
            self.base(toml, **{RUST_SNAPSHOT: "[]\n"}),
            {"src/lib.rs": "pub fn b() {}\n", RUST_SNAPSHOT: '["b"]\n'},
        )
        self.assertEqual(code, 1)

    def test_no_overrides_at_merge_base_exempts_nothing(self):
        code, _ = self.pr({"src/lib.rs": "x\n"}, {"src/lib.rs": "y\n", RUST_SNAPSHOT: "[]\n"})
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
