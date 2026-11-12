import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from dependency_allowlist import main

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
        code, flagged = self.run_main("pylock", PYLOCK_BASE, PYLOCK_HEAD, "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["local-tool", "numpy@2.5.4"])

    def test_npm_reads_nested_scoped_and_aliased_packages_and_skips_links(self):
        code, flagged = self.run_main("npm", json.dumps(NPM_BASE), json.dumps(NPM_HEAD), "")
        self.assertEqual(code, 1)
        self.assertEqual(flagged, ["@scope/util@2.0.0", "left-pad@1.2.0", "real-shim@3.1.0"])

    def test_scoped_npm_packages_match_by_name_and_by_name_and_version(self):
        for allowlist in (
            '[bans]\nallow = ["@scope/util", "left-pad@1.2.0", "real-shim"]\n',
            '[bans]\nallow = ["@scope/util@2.0.0", "left-pad@1.2.0", { crate = "real-shim@3.1.0" }]\n',
        ):
            code, flagged = self.run_main("npm", json.dumps(NPM_BASE), json.dumps(NPM_HEAD), allowlist)
            self.assertEqual((code, flagged), (0, []), allowlist)

    def test_unchanged_and_removed_packages_pass(self):
        code, flagged = self.run_main("pylock", PYLOCK_HEAD, PYLOCK_BASE, "")
        self.assertEqual((code, flagged), (0, []))

    def test_allow_by_name_matches_every_version(self):
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, '[bans]\nallow = ["serde", "libc"]\n')
        self.assertEqual((code, flagged), (0, []))

    def test_allow_by_name_and_version_matches_that_version_only(self):
        code, flagged = self.run_main(
            "cargo", CARGO_BASE, CARGO_HEAD, '[bans]\nallow = ["serde@1.0.228", "libc@0.2.177"]\n'
        )
        self.assertEqual((code, flagged), (1, ["serde@1.0.229"]))

    def test_cargo_deny_table_entries(self):
        allowlist = '[bans]\nallow = [{ crate = "serde@1.0.229" }, { name = "libc", version = "0.2.177" }]\n'
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, allowlist)
        self.assertEqual((code, flagged), (0, []))

    def test_other_allowlist_keys_are_ignored(self):
        allowlist = '[bans.build]\nallow-build-scripts = ["libc"]\n\n[sources]\nallow-git = ["https://example.com/x"]\n'
        code, flagged = self.run_main("cargo", CARGO_BASE, CARGO_HEAD, allowlist)
        self.assertEqual((code, flagged), (1, ["libc@0.2.177", "serde@1.0.229"]))

    def test_missing_base_lock_makes_every_package_new(self):
        code, flagged = self.run_main("pylock", None, PYLOCK_BASE, None)
        self.assertEqual((code, flagged), (1, ["click@8.5.1", "humantyping@1.0.3"]))

    def test_missing_allowlist_file_allows_nothing(self):
        code, flagged = self.run_main("pylock", PYLOCK_BASE, PYLOCK_HEAD, None)
        self.assertEqual((code, flagged), (1, ["local-tool", "numpy@2.5.4"]))

    def test_message_names_the_entry_and_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            head, allow = Path(tmp, "head"), Path(tmp, "allow.toml")
            head.write_text(CARGO_HEAD)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["cargo", str(allow), str(Path(tmp, "missing")), str(head)])
            self.assertIn(f'libc@0.2.177: not in bans.allow of {allow}; add "libc@0.2.177"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
