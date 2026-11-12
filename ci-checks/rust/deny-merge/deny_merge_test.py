import tempfile
import tomllib
import unittest
from pathlib import Path

from deny_merge import main

SHARED = Path(__file__).parent.parent / "deny.toml"
LICENSES = Path(__file__).parent.parent.parent / "licenses.toml"

PROJECT = """\
[bans]
allow = ["serde@1.0.228", { name = "libc" }]

[bans.build]
allow-build-scripts = ["libc"]

[sources]
allow-git = ["https://github.com/example/repo"]

[[licenses.exceptions]]
crate = "ring"
allow = ["OpenSSL"]
"""


class Merging(unittest.TestCase):
    def run_main(self, project: str) -> tuple[int, dict | None]:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp, "allowlist.toml")
            project_path.write_text(project)
            out = Path(tmp, "deny.toml")
            code = main([str(SHARED), str(LICENSES), str(project_path), str(out)])
            return code, tomllib.loads(out.read_text()) if out.exists() else None

    def test_allowlists_are_appended_and_shared_rules_kept(self):
        code, merged = self.run_main(PROJECT)
        self.assertEqual(code, 0)
        shared = tomllib.loads(SHARED.read_text())
        self.assertEqual(merged["bans"]["allow"], ["serde@1.0.228", {"name": "libc"}])
        self.assertEqual(merged["bans"]["build"]["allow-build-scripts"], ["libc"])
        self.assertEqual(merged["sources"]["allow-git"], ["https://github.com/example/repo"])
        self.assertEqual(merged["licenses"]["exceptions"], [{"crate": "ring", "allow": ["OpenSSL"]}])
        self.assertEqual(merged["licenses"]["allow"], tomllib.loads(LICENSES.read_text())["allow"])
        self.assertEqual(merged["licenses"]["private"], shared["licenses"]["private"])
        self.assertEqual(merged["sources"]["unknown-git"], "deny")
        self.assertEqual(merged["sources"]["allow-registry"], shared["sources"]["allow-registry"])
        self.assertEqual(merged["advisories"], shared["advisories"])
        self.assertEqual(merged["graph"], shared["graph"])

    def test_empty_allowlist_reproduces_shared_config_with_license_allow(self):
        code, merged = self.run_main("")
        self.assertEqual(code, 0)
        expected = tomllib.loads(SHARED.read_text())
        expected["licenses"]["allow"] = tomllib.loads(LICENSES.read_text())["allow"]
        self.assertEqual(merged, expected)

    def test_rule_keys_cannot_be_loosened(self):
        for project in (
            '[licenses]\nallow = ["GPL-3.0"]\n',
            '[sources]\nunknown-git = "allow"\n',
            '[advisories]\nignore = ["RUSTSEC-2020-0001"]\n',
        ):
            code, merged = self.run_main(project)
            self.assertEqual(code, 2, project)
            self.assertIsNone(merged)

    def test_allowlist_key_must_be_an_array(self):
        code, _ = self.run_main('[bans]\nallow = "serde"\n')
        self.assertEqual(code, 2)

    def test_missing_project_file_is_an_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "deny.toml")
            self.assertEqual(main([str(SHARED), str(LICENSES), str(Path(tmp, "missing.toml")), str(out)]), 0)
            self.assertEqual(tomllib.loads(out.read_text()), self.run_main("")[1])


if __name__ == "__main__":
    unittest.main()
