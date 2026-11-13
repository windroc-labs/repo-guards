import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[4] / "guards/ci-checks/rust/deny-merge"
sys.path.insert(0, str(SOURCE))

from deny_merge import main

SHARED = SOURCE.parent / "deny.toml"
LICENSES = SOURCE.parents[1] / "licenses.toml"


class Merging(unittest.TestCase):
    def run_main(self, shared: Path) -> tuple[int, dict | None]:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "deny.toml")
            code = main([str(shared), str(LICENSES), str(out)])
            return code, tomllib.loads(out.read_text()) if out.exists() else None

    def test_shared_config_gets_the_license_allow_list(self):
        code, merged = self.run_main(SHARED)
        self.assertEqual(code, 0)
        expected = tomllib.loads(SHARED.read_text())
        expected["licenses"]["allow"] = tomllib.loads(LICENSES.read_text())["allow"]
        self.assertEqual(merged, expected)

    def test_a_license_allow_list_in_deny_toml_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp, "shared.toml")
            shared.write_text('[licenses]\nallow = ["MIT"]\n')
            code, merged = self.run_main(shared)
        self.assertEqual(code, 0)
        self.assertEqual(merged, {"licenses": {"allow": ["MIT"]}})

    def test_shared_config_defaults(self):
        shared = tomllib.loads(SHARED.read_text())
        self.assertEqual(shared["licenses"]["exceptions"], [])
        self.assertEqual(shared["sources"]["allow-git"], [])
        for crate in ("libc", "serde", "proc-macro2", "quote", "getrandom"):
            self.assertIn(crate, shared["bans"]["build"]["allow-build-scripts"])

    def test_missing_or_broken_input_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp, "broken.toml")
            broken.write_text("[licenses\n")
            for shared in (Path(tmp, "missing.toml"), broken):
                code, merged = self.run_main(shared)
                self.assertEqual((code, merged), (2, None), shared)

    def test_usage(self):
        self.assertEqual(main([str(SHARED), str(LICENSES)]), 2)


if __name__ == "__main__":
    unittest.main()
