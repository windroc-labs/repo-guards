import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

from inline_exemptions import main
from inline_exemptions_test import make_repo
from rust_source import extract
from syntax import SourceError


def hits(source: str, path: str = "src/lib.rs") -> list[tuple[str, str, str]]:
    return extract(path, textwrap.dedent(source).encode())


class FormsTest(unittest.TestCase):
    def test_s6_allow_by_rule_and_method(self) -> None:
        source = """\
            struct Foo;

            impl Foo {
                #[allow(clippy::unwrap_used, dead_code)]
                fn bar(&self) {}
            }
            """
        self.assertEqual(
            hits(source, "src/a.rs"), [("clippy", "clippy::unwrap_used", "Foo::bar"), ("rustc", "dead_code", "Foo::bar")]
        )

    def test_s7_forms(self) -> None:
        rows = [
            ("#![allow(unused)]\n", "rustc", "unused"),
            ("#[expect(clippy::panic)]\nfn f() {}\n", "clippy", "clippy::panic"),
            ("#[rustfmt::skip]\nfn f() {}\n", "rustfmt", "skip"),
            ("#[cfg_attr(coverage_nightly, coverage(off))]\nfn f() {}\n", "cargo-llvm-cov", "off"),
            ("#[ignore]\nfn f() {}\n", "cargo-test", "ignore"),
            ("#[cfg_attr(miri, ignore)]\nfn f() {}\n", "cargo-test", "ignore"),
            ("#[no_sanitize(address)]\nfn f() {}\n", "sanitizer", "address"),
            ("// @generated\nfn f() {}\n", "generated", "generated"),
        ]
        for source, tool, rule in rows:
            with self.subTest(source):
                self.assertEqual([hit[:2] for hit in hits(source)], [(tool, rule)])

    def test_rule_lists_and_values(self) -> None:
        rows = [
            ("#[allow()]\nfn f() {}\n", [("rustc", "*", "f")]),
            ('#[ignore = "slow"]\nfn f() {}\n', [("cargo-test", "ignore", "f")]),
            ('#[sanitize(address = "off", thread = "on")]\nfn f() {}\n', [("sanitizer", "address", "f")]),
            ("#[no_sanitize(address, memory)]\nfn f() {}\n", [("sanitizer", "address", "f"), ("sanitizer", "memory", "f")]),
            ("#[cfg_attr(a, cfg_attr(b, allow(dead_code), ignore))]\nfn f() {}\n", [("rustc", "dead_code", "f"), ("cargo-test", "ignore", "f")]),
            ('#[expect(clippy::all, reason = "x")]\nfn f() {}\n', [("clippy", "clippy::all", "f")]),
            ("#[allow(clippy :: x, rustdoc::broken_intra_doc_links)]\nfn f() {}\n", [("clippy", "clippy::x", "f"), ("rustc", "rustdoc::broken_intra_doc_links", "f")]),
            ("#[rustfmt::skip::macros(m)]\nfn f() {}\n", [("rustfmt", "skip", "f")]),
            ("#[cfg_attr(not(test), coverage(on))]\n#[warn(x)]\n#[deny(y)]\n#[forbid(z)]\nfn f() {}\n", []),
        ]
        for source, expected in rows:
            with self.subTest(source):
                self.assertEqual(hits(source), expected)

    def test_s29_doc_comments_and_strings(self) -> None:
        source = """\
            /// #[allow(dead_code)]
            /// jscpd:ignore-start
            //! @generated
            /** #[allow(dead_code)] */
            fn f() {
                // #[allow(dead_code)]
                let s = "#[allow(dead_code)]";
            }
            """
        self.assertEqual(hits(source, "src/a.rs"), [])

    def test_s10_jscpd(self) -> None:
        source = "fn f() {\n    // jscpd:ignore-start\n    let x = 1;\n    // jscpd:ignore-end\n}\n"
        self.assertEqual(hits(source, "src/a.rs"), [("jscpd", "ignore", "f")])
        self.assertEqual(hits("/* jscpd:ignore-start */\nfn f() {}\n// jscpd:ignore-end\n"), [("jscpd", "ignore", "f")])

    def test_generated_header(self) -> None:
        self.assertEqual(hits("#!/usr/bin/env run-cargo-script\n// @generated\nfn f() {}\n"), [("generated", "generated", "")])
        self.assertEqual(hits("fn f() {}\n// @generated\n"), [])

    def test_macro_rules_body(self) -> None:
        source = """\
            macro_rules! m {
                ($x:ident) => {
                    #[allow(dead_code)]
                    fn $x() { #![cfg_attr(miri, ignore)] }
                };
            }

            fn g() {
                macro_rules! n { () => { #[rustfmt::skip] let a = 1; } }
            }
            """
        self.assertEqual(hits(source), [("rustc", "dead_code", ""), ("cargo-test", "ignore", ""), ("rustfmt", "skip", "g")])


class FunctionsTest(unittest.TestCase):
    def test_s47_outside_functions_and_inline_mod(self) -> None:
        rows = [
            ("#[allow(dead_code)]\nimpl Foo {\n    fn bar(&self) {}\n}\n", ""),
            ("struct S {\n    #[allow(dead_code)]\n    x: i32,\n}\n", ""),
            ("mod m {\n    #[allow(dead_code)]\n    fn f() {}\n}\n", "f"),
        ]
        for source, function in rows:
            with self.subTest(source):
                self.assertEqual(hits(source, "src/a.rs"), [("rustc", "dead_code", function)])

    def test_owners_and_nesting(self) -> None:
        source = """\
            impl<T> fmt::Display for a::Foo<T> {
                // a comment between
                #[allow(a)]
                fn fmt(&self) {
                    #![allow(b)]
                    #[allow(c)]
                    let x = 1;
                    fn inner() { #[allow(d)] let y = || { #[allow(e)] let z = 2; }; }
                }
            }

            trait T {
                #[ignore]
                fn g();
            }

            enum E {
                #[allow(f)]
                A,
            }
            """
        self.assertEqual(
            hits(source),
            [
                ("rustc", "a", "Foo::fmt"),
                ("rustc", "b", "Foo::fmt"),
                ("rustc", "c", "Foo::fmt"),
                ("rustc", "d", "inner"),
                ("rustc", "e", "inner"),
                ("cargo-test", "ignore", "T::g"),
                ("rustc", "f", ""),
            ],
        )


class TestModulesTest(unittest.TestCase):
    def test_s13_test_module_not_counted(self) -> None:
        source = """\
            #[cfg(test)]
            #[cfg_attr(coverage_nightly, coverage(off))]
            mod tests {
                fn t() {}
            }

            #[cfg_attr(coverage_nightly, coverage(off))]
            fn slow() {}
            """
        self.assertEqual(hits(source), [("cargo-llvm-cov", "off", "slow")])

    def test_s27_other_names_and_mod_declarations(self) -> None:
        unit_tests = "#[cfg_attr(coverage_nightly, coverage(off))]\n#[cfg(test)]\nmod unit_tests {}\n"
        prod = '#[cfg(any(test, feature = "full"))]\n#[cfg_attr(coverage_nightly, coverage(off))]\nmod prod {}\n'
        helpers = "#[cfg(test)]\n#[cfg_attr(coverage_nightly, coverage(off))]\nmod helpers;\n"
        self.assertEqual(hits(unit_tests + prod + helpers), [("cargo-llvm-cov", "off", "")])
        self.assertEqual(hits(unit_tests + helpers), [])

    def test_inner_attribute_and_other_cfgs(self) -> None:
        rows = [
            ("#[cfg(test)]\nmod tests {\n    #![cfg_attr(coverage_nightly, coverage(off))]\n}\n", []),
            ("#[cfg( test )]\n#[coverage(off)]\nmod tests {}\n", []),
            ("#[cfg(all(test, unix))]\n#[coverage(off)]\nmod tests {}\n", [("cargo-llvm-cov", "off", "")]),
            ("mod tests {\n    #![coverage(off)]\n}\n", [("cargo-llvm-cov", "off", "")]),
            ("#[cfg(test)]\nmod tests {\n    #[coverage(off)]\n    fn t() {}\n}\n", [("cargo-llvm-cov", "off", "t")]),
            ("#[cfg(test)]\n#[cfg_attr(miri, ignore, coverage(off))]\nmod tests {}\n", [("cargo-test", "ignore", "")]),
        ]
        for source, expected in rows:
            with self.subTest(source):
                self.assertEqual(hits(source), expected)


class ErrorTest(unittest.TestCase):
    def test_s23_syntax_error(self) -> None:
        with self.assertRaises(SourceError) as raised:
            hits("fn f( {\n", "src/z.rs")
        self.assertIn("syntax error", str(raised.exception))

    def test_s23_not_utf8_named_by_the_command(self) -> None:
        repo = make_repo({"src/y.rs": b"fn f() { let s = \"\xff\"; }\n", "src/z.rs": "fn f( {\n"})
        settings = Path(tempfile.mkdtemp()) / "settings.toml"
        settings.write_text("", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--repo", str(repo), "--settings", str(settings)])
        self.assertEqual(code, 2)
        self.assertIn('error: "src/y.rs": not UTF-8', out.getvalue())
        self.assertIn('error: "src/z.rs": syntax error at line 1', out.getvalue())


if __name__ == "__main__":
    unittest.main()
