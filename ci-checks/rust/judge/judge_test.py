import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from judge import main


def line(**message) -> str:
    return json.dumps(message)


ARTIFACT = line(reason="compiler-artifact", package_id="demo 0.1.0")
FINISHED_OK = line(reason="build-finished", success=True)
FINISHED_FAIL = line(reason="build-finished", success=False)


def diagnostic(level: str) -> str:
    return line(reason="compiler-message", message={"level": level, "rendered": f"{level}: rendered text\n"})


class Judging(unittest.TestCase):
    def run_main(self, command: str, *contents: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, content in enumerate(contents):
                path = Path(tmp, f"file{index}")
                path.write_text(content)
                paths.append(str(path))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([command, *paths])
            return code, out.getvalue()


class Cargo(Judging):
    def test_clean_build_passes(self):
        self.assertEqual(self.run_main("cargo", f"{ARTIFACT}\n{diagnostic('note')}\n{FINISHED_OK}\n"), (0, ""))

    def test_warning_fails_and_prints_rendered(self):
        code, out = self.run_main("cargo", f"{diagnostic('warning')}\n{FINISHED_OK}\n")
        self.assertEqual(code, 1)
        self.assertIn("warning: rendered text", out)

    def test_error_and_ice_fail(self):
        for level in ("error", "error: internal compiler error"):
            with self.subTest(level=level):
                code, out = self.run_main("cargo", f"{diagnostic(level)}\n{FINISHED_OK}\n")
                self.assertEqual(code, 1)
                self.assertIn(f"{level}: rendered text", out)

    def test_failed_build_fails(self):
        code, out = self.run_main("cargo", f"{ARTIFACT}\n{FINISHED_FAIL}\n")
        self.assertEqual(code, 1)
        self.assertIn("build-finished reports failure", out)

    def test_missing_build_finished_fails(self):
        code, out = self.run_main("cargo", f"{ARTIFACT}\n")
        self.assertEqual(code, 1)
        self.assertIn("no build-finished message", out)

    def test_empty_file_fails(self):
        self.assertEqual(self.run_main("cargo", "")[0], 1)

    def test_non_json_line_fails(self):
        code, out = self.run_main("cargo", f"hello from a build script\n{FINISHED_OK}\n")
        self.assertEqual(code, 1)
        self.assertIn("not JSON", out)

    def test_non_object_line_fails(self):
        self.assertEqual(self.run_main("cargo", f"[1]\n{FINISHED_OK}\n")[0], 1)

    def test_success_must_be_true(self):
        self.assertEqual(self.run_main("cargo", line(reason="build-finished", success="true") + "\n")[0], 1)

    def test_missing_file_fails(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["cargo", "/nonexistent/check.json"])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", out.getvalue())

    def test_any_failing_file_fails(self):
        self.assertEqual(self.run_main("cargo", f"{FINISHED_OK}\n", f"{FINISHED_FAIL}\n")[0], 1)


class CargoConfig(Judging):
    def test_harmless_config_passes(self):
        config = '[build]\njobs = 4\n[target.x86_64-unknown-linux-gnu]\nrunner = "true"\n[env]\nFOO = "bar"\n'
        self.assertEqual(self.run_main("cargo-config", config), (0, ""))

    def test_no_files_passes(self):
        self.assertEqual(self.run_main("cargo-config"), (0, ""))

    def test_forbidden_keys_fail_at_any_depth(self):
        configs = {
            "rustflags": '[build]\nrustflags = ["-Awarnings"]\n',
            "rustdocflags": "[build]\nrustdocflags = []\n",
            "rustc": '[build]\nrustc = "./rustc"\n',
            "rustc-wrapper": 'build.rustc-wrapper = "./w"\n',
            "rustc-workspace-wrapper": '[build]\nrustc-workspace-wrapper = "./w"\n',
            "rustdoc": '[build]\nrustdoc = "./d"\n',
            "alias": '[alias]\nclippy = "check"\n',
        }
        configs["nested rustflags"] = "[target.'cfg(all())']\nrustflags = []\n"
        for name, config in configs.items():
            with self.subTest(name=name):
                code, out = self.run_main("cargo-config", config)
                self.assertEqual(code, 1)
                self.assertIn("is not allowed", out)

    def test_cap_lints_in_any_string_fails(self):
        for config in ('[env]\nX = "--cap-lints=allow"\n', '[target.x]\nrunner = ["sh", "--cap-lints"]\n'):
            with self.subTest(config=config):
                code, out = self.run_main("cargo-config", config)
                self.assertEqual(code, 1)
                self.assertIn("cap-lints", out)

    def test_parse_error_fails(self):
        code, out = self.run_main("cargo-config", "[build\n")
        self.assertEqual(code, 1)
        self.assertIn("cannot parse", out)

    def test_missing_file_fails(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["cargo-config", "/nonexistent/config.toml"]), 1)


if __name__ == "__main__":
    unittest.main()
