import unittest

from config_files import extract
from syntax import SourceError


def hits(path: str, text: str, tracked: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    return extract(path, text.encode(), [path, *tracked])


class CargoTest(unittest.TestCase):
    def test_s11_cargo_machete(self) -> None:
        text = '[package.metadata.cargo-machete]\nignored = ["serde", "log"]\n'
        self.assertEqual(hits("Cargo.toml", text), [("cargo-machete", "serde", ""), ("cargo-machete", "log", "")])

    def test_s36_lint_allow(self) -> None:
        text = '[lints.clippy]\nunwrap_used = "allow"\n\n[workspace.lints.rust]\ndead_code = "allow"\n'
        self.assertEqual(hits("Cargo.toml", text), [("clippy", "clippy::unwrap_used", ""), ("rustc", "dead_code", "")])

    def test_lint_forms(self) -> None:
        text = (
            "[lints]\nworkspace = true\n\n[lints.rust]\n"
            'unexpected_cfgs = { level = "allow", check-cfg = ["cfg(x)"] }\nunsafe_code = "warn"\n'
            'missing_docs = { level = "deny" }\n\n[lints.rustdoc]\nbroken_intra_doc_links = "allow"\n'
            '\n[workspace.metadata.cargo-machete]\nignored = ["anyhow"]\n'
        )
        self.assertEqual(
            hits("crates/a/Cargo.toml", text),
            [("rustc", "unexpected_cfgs", ""), ("rustc", "rustdoc::broken_intra_doc_links", ""), ("cargo-machete", "anyhow", "")],
        )

    def test_invalid_toml(self) -> None:
        with self.assertRaises(SourceError):
            hits("Cargo.toml", "[lints\n")


class PyprojectTest(unittest.TestCase):
    def test_s48_deptry(self) -> None:
        text = '[tool.deptry]\nignore = ["DEP001"]\nper_rule_ignores = { DEP002 = ["requests"] }\n'
        self.assertEqual(hits("pyproject.toml", text), [("deptry", "DEP001", ""), ("deptry", "DEP002:requests", "")])

    def test_no_deptry(self) -> None:
        self.assertEqual(hits("pyproject.toml", '[project]\nname = "x"\n'), [])


class GoModTest(unittest.TestCase):
    def test_s43_ignore_and_nested_module(self) -> None:
        tracked = ("tools/go.mod",)
        self.assertEqual(hits("go.mod", "module x\n\ngo 1.25\n\nignore ./scratch\n", tracked), [("go-mod", "./scratch", "")])
        self.assertEqual(extract("tools/go.mod", b"module x/tools\n", ["go.mod", *tracked]), [("go-mod", "module", "")])

    def test_ignore_block_quotes_and_comments(self) -> None:
        text = 'module x\n\nignore (\n\t./a // old\n\t"./b c"\n)\n// ignore ./no\n'
        self.assertEqual(hits("go.mod", text), [("go-mod", "./a", ""), ("go-mod", "./b c", "")])

    def test_sibling_module_is_not_nested(self) -> None:
        self.assertEqual(extract("b/go.mod", b"module b\n", ["a/go.mod", "b/go.mod", "bb/go.mod"]), [])


if __name__ == "__main__":
    unittest.main()
