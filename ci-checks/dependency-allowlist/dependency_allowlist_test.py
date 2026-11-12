import contextlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from dependency_allowlist import main

DEFAULT = Path(__file__).parent / "allowlist.toml"

CARGO_BASE = """\
version = 4

[[package]]
name = "app"
version = "0.1.0"

[[package]]
name = "serde"
version = "1.0.228"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""

CARGO_HEAD = """\
version = 4

[[package]]
name = "app"
version = "0.2.0"

[[package]]
name = "serde"
version = "1.0.229"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "libc"
version = "0.2.177"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""

PYLOCK_BASE = """\
lock-version = "1.0"
created-by = "uv"

[[packages]]
name = "click"
version = "8.5.1"

[[packages]]
name = "humantyping"
version = "1.0.3"
vcs = { type = "git", url = "https://example.com/humantyping", commit-id = "db14" }
"""

PYLOCK_HEAD = """\
lock-version = "1.0"
created-by = "uv"

[[packages]]
name = "click"
version = "8.5.1"

[[packages]]
name = "humantyping"
version = "1.0.3"
vcs = { type = "git", url = "https://example.com/humantyping", commit-id = "db14" }

[[packages]]
name = "numpy"
version = "2.5.4"

[[packages]]
name = "local-tool"
directory = { path = "tools/local" }
"""

UV_LOCK_BASE = """\
version = 1

[[package]]
name = "demo-app"
version = "0.1.0"
source = { virtual = "." }

[[package]]
name = "click"
version = "8.5.1"
source = { registry = "https://pypi.org/simple" }
"""

UV_LOCK_HEAD = """\
version = 1

[[package]]
name = "demo-app"
version = "0.2.0"
source = { editable = "." }

[[package]]
name = "click"
version = "8.5.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "torch"
version = "2.9.0"
source = { registry = "https://download.pytorch.org/whl/cpu" }

[[package]]
name = "torch"
version = "2.9.0+cu128"
source = { registry = "https://download.pytorch.org/whl/cu128" }
"""

NPM_BASE = {
    "name": "app",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "app", "version": "1.0.0"},
        "node_modules/left-pad": {"version": "1.3.0"},
    },
}

NPM_HEAD = {
    "name": "app",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "app", "version": "1.1.0"},
        "node_modules/left-pad": {"version": "1.3.0"},
        "node_modules/@scope/util": {"version": "2.0.0"},
        "node_modules/@scope/util/node_modules/left-pad": {"version": "1.2.0"},
        "node_modules/shim": {"name": "real-shim", "version": "3.1.0"},
        "node_modules/workspace-a": {"resolved": "packages/a", "link": True},
        "packages/a": {"name": "workspace-a", "version": "0.0.1"},
    },
}


def go_list(*modules: object) -> str:
    return "\n".join(json.dumps(m, indent="\t") for m in modules) + "\n"


GO_MAIN = {"Path": "example.com/app", "Main": True, "Dir": "/src/app", "GoVersion": "1.16"}
GO_LOCAL = {"Path": "example.com/lib", "Version": "v0.0.0", "Replace": {"Path": "../lib", "Dir": "/src/lib"}}


def go_fork(version: str) -> dict[str, object]:
    fork = {"Path": "example.com/fork/text", "Version": version}
    return {"Path": "golang.org/x/text", "Version": "v0.3.0", "Replace": fork}


GO_BASE = go_list(
    GO_MAIN,
    {"Path": "github.com/google/uuid", "Version": "v1.6.0"},
    go_fork("v0.3.1"),
    GO_LOCAL,
)

GO_HEAD = go_list(
    GO_MAIN,
    {"Path": "github.com/google/uuid", "Version": "v1.6.0"},
    go_fork("v0.3.2"),
    GO_LOCAL,
    {"Path": "example.com/tools", "Version": "v1.0.0", "Replace": {"Path": "./tools", "Dir": "/src/app/tools"}},
    {"Path": "rsc.io/quote", "Version": "v1.5.2"},
    {"Path": "rsc.io/sampler", "Version": "v1.3.0", "Indirect": True},
)


class Check(unittest.TestCase):
    def run_main(self, fmt: str, base: str | None, head: str, allowlist: str | None) -> tuple[int, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            base_path, head_path, allow_path = Path(tmp, "base"), Path(tmp, "head"), Path(tmp, "allow.toml")
            if base is not None:
                base_path.write_text(base)
            head_path.write_text(head)
            if allowlist is not None:
                allow_path.write_text(allowlist)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([fmt, str(allow_path), str(base_path), str(head_path)])
            return code, [line.split(":")[0] for line in out.getvalue().splitlines()]

    def test_cargo_reports_new_and_changed_registry_crates_only(self):
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["libc@0.2.177", "serde@1.0.229"])

    def test_pylock_reports_new_packages_and_keeps_versionless_ones(self):
        code, flagged = self.run_main("python", PYLOCK_BASE, PYLOCK_HEAD, "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["local-tool", "numpy@2.5.4"])

    def test_uv_lock_reports_every_extra_and_group_package_and_skips_the_workspace(self):
        code, flagged = self.run_main("python", UV_LOCK_BASE, UV_LOCK_HEAD, "")
        self.assertEqual((code, flagged), (1, ["torch@2.9.0", "torch@2.9.0+cu128"]))

    def test_python_compares_a_uv_lock_base_with_a_pylock_head(self):
        code, flagged = self.run_main("python", UV_LOCK_BASE, PYLOCK_BASE, "")
        self.assertEqual((code, flagged), (1, ["humantyping@1.0.3"]))

    def test_npm_reads_nested_scoped_and_aliased_packages_and_skips_links(self):
        code, flagged = self.run_main("npm", json.dumps(NPM_BASE), json.dumps(NPM_HEAD), "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["@scope/util@2.0.0", "left-pad@1.2.0", "real-shim@3.1.0"])

    def test_scoped_npm_packages_match_by_name_and_by_name_and_version(self):
        for allowlist in (
            'allow = ["@scope/util", "left-pad@1.2.0", "real-shim"]\n',
            'allow = ["@scope/util@2.0.0", "left-pad@1.2.0", { crate = "real-shim@3.1.0" }]\n',
        ):
            code, flagged = self.run_main("npm", json.dumps(NPM_BASE), json.dumps(NPM_HEAD), allowlist)
            self.assertEqual((code, flagged), (0, []), allowlist)

    def test_go_reports_new_and_transitive_modules_under_their_replacement(self):
        code, flagged = self.run_main("go", GO_BASE, GO_HEAD, "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["example.com/fork/text@v0.3.2", "rsc.io/quote@v1.5.2", "rsc.io/sampler@v1.3.0"])

    def test_go_without_base_skips_the_main_module_and_local_replaces(self):
        code, flagged = self.run_main("go", None, GO_BASE, "allow = []\n")
        self.assertEqual((code, flagged), (1, ["example.com/fork/text@v0.3.1", "github.com/google/uuid@v1.6.0"]))

    def test_go_allowlist_entry_forms(self):
        for allowlist in (
            'allow = ["rsc.io/quote", "rsc.io/sampler@v1.3.0", "example.com/fork/text"]\n',
            (
                'allow = [{ crate = "rsc.io/quote@v1.5.2" }, { name = "rsc.io/sampler" },'
                ' { name = "example.com/fork/text", version = "v0.3.2" }]\n'
            ),
        ):
            code, flagged = self.run_main("go", GO_BASE, GO_HEAD, allowlist)
            self.assertEqual((code, flagged), (0, []), allowlist)

    def test_go_allowing_the_replaced_path_does_not_allow_the_replacement(self):
        allowlist = 'allow = ["golang.org/x/text", "rsc.io/quote", "rsc.io/sampler"]\n'
        code, flagged = self.run_main("go", GO_BASE, GO_HEAD, allowlist)
        self.assertEqual((code, flagged), (1, ["example.com/fork/text@v0.3.2"]))

    def test_go_message_names_the_entry_and_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head, allow = Path(tmp, "base"), Path(tmp, "head"), Path(tmp, "allow.toml")
            base.write_text(GO_BASE)
            head.write_text(GO_HEAD)
            allow.write_text("allow = []\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["go", str(allow), str(base), str(head)])
            message = f'rsc.io/quote@v1.5.2: not in allow of {allow}; add "rsc.io/quote@v1.5.2"'
            self.assertIn(message, out.getvalue())

    def test_default_allowlist_file_is_empty(self):
        self.assertEqual(tomllib.loads(DEFAULT.read_text()), {"allow": []})

    def test_unchanged_and_removed_packages_pass(self):
        code, flagged = self.run_main("python", PYLOCK_HEAD, PYLOCK_BASE, "")
        self.assertEqual((code, flagged), (0, []))

    def test_allow_by_name_matches_every_version(self):
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, 'allow = ["serde", "libc"]\n')
        self.assertEqual((code, flagged), (0, []))

    def test_allow_by_name_and_version_matches_that_version_only(self):
        code, flagged = self.run_main(
            "cargo", CARGO_BASE, CARGO_HEAD, 'allow = ["serde@1.0.228", "libc@0.2.177"]\n'
        )
        self.assertEqual((code, flagged), (1, ["serde@1.0.229"]))

    def test_cargo_deny_table_entries(self):
        allowlist = 'allow = [{ crate = "serde@1.0.229" }, { name = "libc", version = "0.2.177" }]\n'
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, allowlist)
        self.assertEqual((code, flagged), (0, []))

    def test_other_keys_are_ignored(self):
        allowlist = '[bans]\nallow = ["libc"]\n\n[sources]\nallow-git = ["https://example.com/x"]\n'
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, allowlist)
        self.assertEqual((code, flagged), (1, ["libc@0.2.177", "serde@1.0.229"]))

    def test_missing_base_lock_makes_every_package_new(self):
        code, flagged = self.run_main("python", None, PYLOCK_BASE, "allow = []\n")
        self.assertEqual((code, flagged), (1, ["click@8.5.1", "humantyping@1.0.3"]))

    def test_empty_allowlist_allows_nothing(self):
        code, flagged = self.run_main("python", PYLOCK_BASE, PYLOCK_HEAD, "allow = []\n")
        self.assertEqual((code, flagged), (1, ["local-tool", "numpy@2.5.4"]))

    def test_missing_allowlist_file_is_an_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, flagged = self.run_main("python", PYLOCK_BASE, PYLOCK_HEAD, None)
        self.assertEqual((code, flagged), (2, []))
        self.assertIn("dependency_allowlist: ", err.getvalue())

    def test_message_names_the_entry_and_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            head, allow = Path(tmp, "head"), Path(tmp, "allow.toml")
            head.write_text(CARGO_HEAD)
            allow.write_text("allow = []\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["cargo", str(allow), str(Path(tmp, "missing")), str(head)])
            self.assertIn(f'libc@0.2.177: not in allow of {allow}; add "libc@0.2.177"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
