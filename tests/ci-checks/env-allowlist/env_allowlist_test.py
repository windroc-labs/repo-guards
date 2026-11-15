import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARDS = Path(__file__).resolve().parents[3] / "guards"
SOURCE = GUARDS / "ci-checks/env-allowlist"
sys.path.insert(0, str(SOURCE))

from env_allowlist import Error, findings, lex, main, release_tokens

DEFAULT = SOURCE / "allowlist.toml"
ADOPTED = 'allow = []\n[rust]\ntest_paths = ["tests/*", "benches/*", "examples/*", "*_test.rs", "tests.rs"]\n'
OVERRIDES = GUARDS / "ci-checks/config-overrides/config_overrides.py"


def scan(code: str, allow: frozenset[str] = frozenset()) -> list[str]:
    return [f.message for f in findings("a.rs", release_tokens(lex(code)), allow)]


class LexTest(unittest.TestCase):
    def test_comments_and_literals_hide_reads(self) -> None:
        hidden = [
            '// env::var("X")\n',
            '/* outer /* env::var("X") */ env!("X") */\n',
            'let s = "env::var(\\"X\\")";\n',
            'let s = r#"env::var("X") "# ;\n',
            'let s = r##"a "# env::var("X") "##;\n',
            'let s = b"env::var(\\"X\\")";\n',
            'let s = br#"env!("X")"#;\n',
            'let s = c"env!(\\"X\\")";\n',
        ]
        for code in hidden:
            with self.subTest(code=code):
                self.assertEqual(scan(code), [])

    def test_char_and_lifetime(self) -> None:
        code = "fn f<'a>(x: &'a str) -> char { let q = '\"'; let b = b'\\''; 'x' }\nfn g() { env::var(\"X\"); }\n"
        self.assertEqual(scan(code), ["env var X is not in the allowlist"])

    def test_string_token_content(self) -> None:
        tokens = [t for t in lex('x = r#"a"b"#; y = "c\\"d";') if t.kind == "str"]
        self.assertEqual([t.text for t in tokens], ['a"b', 'c\\"d'])

    def test_positions_are_one_based_characters(self) -> None:
        token = next(t for t in lex("é\n  ab") if t.text == "ab")
        self.assertEqual((token.line, token.column), (2, 3))

    def test_raw_identifier(self) -> None:
        self.assertEqual(scan('r#env::r#var("X");'), ["env var X is not in the allowlist"])

    def test_unterminated(self) -> None:
        for code, what in [
            ('"abc', "string"),
            ('r#"abc"', "raw string"),
            ("/* a /* b */", "block comment"),
            ("'\\n", "char literal"),
        ]:
            with self.subTest(code=code), self.assertRaisesRegex(Error, f"line 1: unterminated {what}"):
                lex(code)


class FindingsTest(unittest.TestCase):
    def test_allowed_names_pass(self) -> None:
        code = 'std::env::var("HOME"); env::var_os("HOME"); env!("HOME"); option_env!("HOME"); env!("HOME", "msg");'
        self.assertEqual(scan(code, frozenset({"HOME"})), [])

    def test_unlisted_name_each_form_and_prefix(self) -> None:
        forms = [
            'env::var("DEBUG_MODE")',
            'std::env::var("DEBUG_MODE")',
            '::std::env::var("DEBUG_MODE")',
            'env::var_os("DEBUG_MODE")',
            'env::var::<&str>("DEBUG_MODE")',
            'env!("DEBUG_MODE")',
            'core::env!("DEBUG_MODE")',
            'std::option_env!["DEBUG_MODE"]',
            'option_env!{"DEBUG_MODE"}',
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertEqual(
                    scan(f"fn f() {{ {form}; }}", frozenset({"HOME"})), ["env var DEBUG_MODE is not in the allowlist"]
                )

    def test_non_literal_names(self) -> None:
        for code in [
            "env::var(name);",
            'env::var(&format!("{x}"));',
            'env!(concat!("A", "B"));',
            "x.map(env::var);",
            "let f = std::env::var;",
            "env::var_os(NAME);",
            'env::var("A" + b);',
        ]:
            with self.subTest(code=code):
                self.assertEqual(scan(code, frozenset({"A"})), ["name is not a string literal"])

    def test_vars_always_fail(self) -> None:
        self.assertEqual(
            scan("env::vars(); std::env::vars_os();", frozenset({"A"})),
            [
                'env::vars reads every variable; read each name with env::var("NAME")',
                'env::vars_os reads every variable; read each name with env::var("NAME")',
            ],
        )

    def test_import_forms(self) -> None:
        failing = {
            "use std::env::var;": "import of env::var; call it through the module as env::var",
            "use std::env::{self, var_os};": "import of env::var_os; call it through the module as env::var_os",
            "use std::env::var as getenv;": "import of env::var; call it through the module as env::var",
            "use std::env::*;": "import of env::*; call each function through the module as env::<function>",
            "use std::env as e;": "renamed import of std::env; import it as env",
            "use std::{env as e, fs};": "renamed import of std::env; import it as env",
            "use std::env::{self as e};": "renamed import of std::env; import it as env",
            "pub use std::env::vars;": "import of env::vars; call it through the module as env::vars",
            "use core::option_env as oe;": "renamed import of core::option_env; import it as option_env",
            "use ::std::{fs, env::{var}};": "import of env::var; call it through the module as env::var",
            "use crate::util::env::var;": "import of env::var; call it through the module as env::var",
        }
        for code, message in failing.items():
            with self.subTest(code=code):
                self.assertEqual(scan(code), [message])
        for code in [
            "use std::env;",
            "use std::{env, fs};",
            "use std::env::{self};",
            "use std::env::args;",
            "use crate::config::env as cfg;",
        ]:
            with self.subTest(code=code):
                self.assertEqual(scan(code), [])

    def test_kept_module_name_under_other_prefix(self) -> None:
        code = 'pub use std::env;\nfn f() { crate::util::env::var("X"); s::env::var("Y"); }'
        self.assertEqual(scan(code, frozenset({"Y"})), ["env var X is not in the allowlist"])

    def test_test_module_exempt(self) -> None:
        code = (
            '#[cfg(test)]\n#[allow(unused)]\npub(crate) mod tests {\n    fn t() { env::var("A"); let s = "}"; }\n}\n'
            '#[cfg(test)]\nmod other;\nfn f() { env::var("B"); }\n'
            '#[cfg(test)]\nfn helper() { env::var("C"); }\n'
        )
        self.assertEqual(scan(code), ["env var B is not in the allowlist", "env var C is not in the allowlist"])

    def test_finding_position(self) -> None:
        found = findings(
            "src/a.rs",
            release_tokens(lex('fn f() {\n    let x = std::env::var("X");\n}\nuse std::env::var;')),
            frozenset(),
        )
        self.assertEqual([(f.path, f.line, f.column) for f in found], [("src/a.rs", 2, 23), ("src/a.rs", 4, 1)])

    def test_unrelated_names_pass(self) -> None:
        code = 'fn var(x: u8) {}\nlet env = 1; var(2); x.var("A"); macro_rules! env { () => {} } std::env::args(); std::env::set_var("A", "1");'
        self.assertEqual(scan(code), [])


def git_repo(root: Path, files: dict[str, str]) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


def run(root: Path, config: Path = DEFAULT, language: str = "rust") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.chdir(root), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([language, str(config)])
    return code, out.getvalue(), err.getvalue()


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)

    def config(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix="-config.toml", delete=False)
        with handle:
            handle.write(text)
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def adopted(self) -> Path:
        return self.config(ADOPTED)

    def test_not_adopted_reports_and_passes(self) -> None:
        git_repo(
            self.root,
            {"a.rs": 'fn f() { env::var(name); }\n', "b.rs": 'fn f() { env!("B"); }\n', "c.rs": "fn c() {}\n"},
        )
        code, out, err = run(self.root)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            out,
            "report env allowlist (not adopted, CI does not fail): 2 findings in 2 of 3 files\n"
            "  a.rs:1:15: name is not a string literal\n"
            "  b.rs:1:10: env var B is not in the allowlist\n"
            "To adopt the check: hard-code every name and merge that code, then set allow under "
            '["ci-checks/env-allowlist/allowlist.toml"] in .github/checks/repo-guards.toml (an owner-approved change).\n',
        )

    def test_not_adopted_without_findings(self) -> None:
        git_repo(self.root, {"src/main.rs": "fn main() {}\n"})
        self.assertEqual(run(self.root), (0, "ok env allowlist (not adopted): 1 file checked\n", ""))

    def test_caller_override_without_allow_stays_not_adopted(self) -> None:
        guards = self.root.parent / f"{self.root.name}-guards"
        shutil.copytree(SOURCE, guards / "ci-checks/env-allowlist")
        self.addCleanup(shutil.rmtree, guards)
        overrides = self.config('["ci-checks/env-allowlist/allowlist.toml"]\nrust.test_paths = ["tests/*"]\n')
        subprocess.run(
            [sys.executable, str(OVERRIDES), "apply", str(guards), str(overrides)], check=True, capture_output=True
        )
        git_repo(self.root, {"src/main.rs": 'fn main() { std::env::var("HOME").ok(); }\n'})
        code, out, _ = run(self.root, guards / "ci-checks/env-allowlist/allowlist.toml")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("report env allowlist (not adopted"), out)

    def test_empty_allowlist_fails_any_read(self) -> None:
        git_repo(self.root, {"src/main.rs": 'fn main() { std::env::var("HOME").ok(); }\n'})
        code, out, _ = run(self.root, self.adopted())
        self.assertEqual(code, 1)
        self.assertIn("src/main.rs:1:23: env var HOME is not in the allowlist", out)

    def test_report_format(self) -> None:
        git_repo(
            self.root,
            {
                "b.rs": 'fn f() { env::var("B"); }\n',
                "a.rs": 'use std::env::var;\nfn f() { env!("A"); }\n',
                "c.rs": "fn c() {}\n",
            },
        )
        code, out, _ = run(self.root, self.adopted())
        self.assertEqual(code, 1)
        self.assertEqual(
            out,
            "FAIL env allowlist: 3 findings in 2 of 3 files\n"
            "  a.rs:1:1: import of env::var; call it through the module as env::var\n"
            "  a.rs:2:10: env var A is not in the allowlist\n"
            "  b.rs:1:15: env var B is not in the allowlist\n"
            "Read only allowlisted variables, or add the name to allow under "
            '["ci-checks/env-allowlist/allowlist.toml"] in .github/checks/repo-guards.toml (an owner-approved change).\n',
        )
        config = self.config('allow = ["A", "B"]\n[rust]\ntest_paths = []\n')
        (self.root / "a.rs").write_text('fn f() { env!("A"); }\n')
        code, out, _ = run(self.root, config)
        self.assertEqual((code, out), (0, 'ok env allowlist: 3 files checked, allow=["A", "B"]\n'))

    def test_one_finding_is_singular(self) -> None:
        git_repo(self.root, {"a.rs": 'fn f() { env!("A"); }\n'})
        _, out, _ = run(self.root, self.adopted())
        self.assertTrue(out.startswith("FAIL env allowlist: 1 finding in 1 of 1 file\n"), out)

    def test_test_paths_exempt(self) -> None:
        read = 'fn t() { env::var("T"); }\n'
        git_repo(
            self.root,
            {
                "tests/a.rs": read,
                "crates/x/tests/a.rs": read,
                "src/foo/tests.rs": read,
                "src/a_test.rs": read,
                "benches/b.rs": read,
                "examples/e.rs": read,
                "src/lib.rs": "#[cfg(test)]\nmod tests;\n",
            },
        )
        self.assertEqual(run(self.root, self.adopted()), (0, "ok env allowlist: 1 file checked, allow=[]\n", ""))

    def test_scans_only_the_current_directory(self) -> None:
        git_repo(self.root, {"other/a.rs": 'fn f() { env!("A"); }\n', "app/src/main.rs": "fn main() {}\n"})
        self.assertEqual(run(self.root / "app", self.adopted())[0], 0)

    def test_no_rust_files_pass(self) -> None:
        git_repo(self.root, {"README.md": "x\n"})
        self.assertEqual(run(self.root, self.adopted()), (0, "ok env allowlist: 0 files checked, allow=[]\n", ""))

    def test_caller_override_applies(self) -> None:
        guards = self.root.parent / f"{self.root.name}-guards"
        shutil.copytree(SOURCE, guards / "ci-checks/env-allowlist")
        self.addCleanup(shutil.rmtree, guards)
        overrides = self.config('["ci-checks/env-allowlist/allowlist.toml"]\nallow = ["HOME"]\n')
        subprocess.run(
            [sys.executable, str(OVERRIDES), "apply", str(guards), str(overrides)], check=True, capture_output=True
        )
        config = guards / "ci-checks/env-allowlist/allowlist.toml"
        git_repo(self.root, {"src/main.rs": 'fn main() { std::env::var("HOME").ok(); }\n'})
        self.assertEqual(run(self.root, config), (0, 'ok env allowlist: 1 file checked, allow=["HOME"]\n', ""))
        (self.root / "src/main.rs").write_text('fn main() { std::env::var("PATH").ok(); }\n')
        self.assertEqual(run(self.root, config)[0], 1)

    def test_errors_exit_2(self) -> None:
        git_repo(self.root, {"src/bad.rs": 'fn f() { let s = "open; }\n', "tests/bad.rs": '"open'})
        good = 'allow = []\n[rust]\ntest_paths = ["tests/*"]\n'
        cases = {
            "missing config": (self.root.parent / "missing.toml", "rust", "missing.toml"),
            "bad toml": (self.config("allow = [\n"), "rust", "-config.toml"),
            "allow not strings": (self.config("allow = [1]\n[rust]\ntest_paths = []\n"), "rust", "allow"),
            "allow not a list": (self.config('allow = "HOME"\n[rust]\ntest_paths = []\n'), "rust", "allow"),
            "test_paths missing, not adopted": (self.config("[rust]\n"), "rust", "rust.test_paths"),
            "test_paths missing": (self.config("allow = []\n"), "rust", "rust.test_paths"),
            "test_paths not list": (self.config('allow = []\n[rust]\ntest_paths = "x"\n'), "rust", "rust.test_paths"),
            "unknown language": (self.config(good), "cobol", "usage"),
            "unterminated literal": (self.config(good), "rust", "src/bad.rs: line 1: unterminated string"),
        }
        for name, (config, language, needle) in cases.items():
            with self.subTest(name):
                code, out, err = run(self.root, config, language)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(needle, err)
                self.assertTrue(err.startswith(("env-allowlist: ", "usage: ")), err)

    def test_git_failure_exits_2(self) -> None:
        code, _, err = run(self.root)
        self.assertEqual(code, 2)
        self.assertIn("env-allowlist: git ls-files failed", err)

    def test_default_config(self) -> None:
        text = DEFAULT.read_text()
        self.assertNotIn("allow =", text)
        self.assertIn('test_paths = ["tests/*", "benches/*", "examples/*", "*_test.rs", "tests.rs"]', text)


if __name__ == "__main__":
    unittest.main()
