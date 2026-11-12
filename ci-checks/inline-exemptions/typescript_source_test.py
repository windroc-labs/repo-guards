import textwrap
import unittest

from syntax import SourceError
from typescript_source import extract


def hits(source: str, path: str = "src/a.ts") -> list[tuple[str, str, str]]:
    return extract(path, textwrap.dedent(source).encode())


def in_method(line: str) -> str:
    return f"class C {{\n  run() {{\n    {line}\n  }}\n}}\n"


def in_describe(line: str) -> str:
    return f'import {{ describe, it, test }} from "vitest";\n\ndescribe("d", () => {{\n  {line}\n}});\n'


class FormsTest(unittest.TestCase):
    def test_s37_forms_in_method(self) -> None:
        rows = [
            ("// @ts-expect-error", "tsc", "ts-expect-error"),
            ("// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 说明", "eslint", "@typescript-eslint/no-explicit-any"),
            ('/* eslint no-console: "off" */', "eslint", "no-console"),
            ("// prettier-ignore", "prettier", "ignore"),
            ("/* v8 ignore next */", "vitest-coverage", "next"),
            ("/* istanbul ignore if -- @preserve */", "vitest-coverage", "if"),
            ("/** @public */", "knip", "public"),
            ("ctx.skip()", "vitest", "skip"),
        ]
        for line, tool, rule in rows:
            with self.subTest(line):
                self.assertEqual(hits(in_method(line)), [(tool, rule, "C.run")])

    def test_s38_test_markers_in_describe(self) -> None:
        rows = [
            ('it.skip("x", () => {})', "skip"),
            ('test.concurrent.skip("x", f)', "skip"),
            ('it.todo("x")', "todo"),
            ('it.fails("x", f)', "fails"),
            ('describe.skipIf(isWin)("x", f)', "skipIf"),
            ('xit("x", f)', "xit"),
        ]
        for line, rule in rows:
            with self.subTest(line):
                self.assertEqual(hits(in_describe(line), "src/a.test.ts"), [("vitest", rule, "")])

    def test_s28_vitest_alias(self) -> None:
        source = 'import { it as t } from "vitest";\n\nt.skip("x", f);\n'
        self.assertEqual(hits(source, "src/a.test.ts"), [("vitest", "skip", "")])

    def test_other_test_markers(self) -> None:
        source = """\
            it.skip.each([1])("x", f);
            test.runIf(ok)("y", f);
            suite.todo("z");
            xdescribe("d", f);
            xtest("t", f);
            test("c", (context) => {
              context.skip();
            });
            foo.skip("x");
            """
        self.assertEqual(
            hits(source, "src/a.test.ts"),
            [
                ("vitest", "skip", ""),
                ("vitest", "runIf", ""),
                ("vitest", "todo", ""),
                ("vitest", "xdescribe", ""),
                ("vitest", "xtest", ""),
                ("vitest", "skip", ""),
            ],
        )

    def test_s14_focus_markers_not_counted(self) -> None:
        self.assertEqual(hits('it.only("x", f);\nfit("y", f);\nfdescribe("z", f);\n', "src/a.test.ts"), [])

    def test_tsc_directives(self) -> None:
        source = """\
            /* @ts-nocheck */
            function f() {
              // @TS-IGNORE
              x();
              /** @ts-expect-error */
              y();
              // see @ts-ignore
            }
            """
        self.assertEqual(hits(source), [("tsc", "ts-nocheck", ""), ("tsc", "ts-ignore", "f"), ("tsc", "ts-expect-error", "f")])

    def test_eslint_rule_lists(self) -> None:
        self.assertEqual(hits("x(); // eslint-disable-next-line a, b\n"), [("eslint", "a", ""), ("eslint", "b", "")])
        self.assertEqual(hits("// eslint-disable\nx();\n"), [("eslint", "*", "")])
        self.assertEqual(hits("x(); // eslint-disable-line\n"), [("eslint", "*", "")])
        self.assertEqual(hits("/* eslint-disable a,\n   b -- why */\n"), [("eslint", "a", ""), ("eslint", "b", "")])
        self.assertEqual(hits("/* eslint-enable a */\n/* eslint-env node */\n// eslint no-console: 0\n"), [])

    def test_eslint_inline_config(self) -> None:
        source = """/* eslint no-console: 0, "no-alert": ["off", { "x": 1 }], eqeqeq: ["error", "always"], semi: 'OFF', quotes: "warn" */\n"""
        self.assertEqual(hits(source), [("eslint", "no-console", ""), ("eslint", "no-alert", ""), ("eslint", "semi", "")])

    def test_coverage_forms(self) -> None:
        source = """\
            /* c8 ignore next 3 */
            /* v8 ignore start */
            /* v8 ignore stop */
            /* istanbul ignore else */
            /* v8 ignore file -- @preserve */
            // c8 ignore end
            """
        self.assertEqual(
            hits(source),
            [("vitest-coverage", "next", ""), ("vitest-coverage", "start", ""), ("vitest-coverage", "else", ""), ("vitest-coverage", "file", "")],
        )

    def test_knip_public_only_in_jsdoc(self) -> None:
        source = "/**\n * Exported for plugins.\n * @public\n */\nexport function f() {}\n/* @public */\n// @public\n"
        self.assertEqual(hits(source), [("knip", "public", "f")])

    def test_prettier_ignore_in_jsx(self) -> None:
        source = "const x = <div>{/* prettier-ignore */}text // @ts-ignore</div>;\n"
        self.assertEqual(hits(source, "src/a.tsx"), [("prettier", "ignore", "")])

    def test_s29_strings_regex_and_templates_are_not_comments(self) -> None:
        source = "const s = `// @ts-ignore`;\nconst t = '// eslint-disable';\nconst r = /\\/\\/ @ts-ignore/;\n"
        self.assertEqual(hits(source, "src/b.ts"), [])

    def test_s30_declaration_file(self) -> None:
        self.assertEqual(hits("// @ts-ignore\nexport declare const x: number;\n", "types/y.d.ts"), [("tsc", "ts-ignore", "")])

    def test_grammar_by_suffix(self) -> None:
        self.assertEqual(hits("const y = <number>x;\n", "src/a.ts"), [])
        self.assertEqual(hits("const y = <div>{x}</div>;\n", "src/a.jsx"), [])
        self.assertEqual(hits("export const y = <div>{x}</div>;\n", "src/a.mjs"), [])

    def test_s44_generated_header(self) -> None:
        self.assertEqual(hits("// @generated\nexport const x = 1;\n", "src/gen.ts"), [("generated", "generated", "")])
        self.assertEqual(hits("#!/usr/bin/env node\n/* @generated */\nx();\n"), [("generated", "generated", "")])
        self.assertEqual(hits("x();\n// @generated\n"), [])

    def test_s10_jscpd(self) -> None:
        source = "function f() {\n  // jscpd:ignore-start\n  x();\n  // jscpd:ignore-end\n}\n"
        self.assertEqual(hits(source, "src/a.ts"), [("jscpd", "ignore", "f")])


class FunctionsTest(unittest.TestCase):
    def test_s39_function_names(self) -> None:
        rows = [
            ("function f() {\n  // @ts-ignore\n  x();\n}\n", "f"),
            ("const g = () => {\n  // @ts-ignore\n  x();\n};\n", "g"),
            ("class C {\n  h = () => {\n    // @ts-ignore\n    x();\n  };\n}\n", "C.h"),
            ("const o = {\n  m() {\n    // @ts-ignore\n    x();\n  },\n};\n", "o.m"),
            ("export default function () {\n  // @ts-ignore\n  x();\n}\n", "default"),
            ("function f() {\n  [1].map(() => {\n    // @ts-ignore\n    x();\n  });\n}\n", "f"),
        ]
        for source, function in rows:
            with self.subTest(function):
                self.assertEqual(hits(source), [("tsc", "ts-ignore", function)])

    def test_other_names(self) -> None:
        source = """\
            class C {
              static get g() { return x(); } // @ts-ignore
              set s(v) { /* @ts-ignore */ }
              constructor() { /* @ts-ignore */ }
              #p() { /* @ts-ignore */ }
              static q = function () { /* @ts-ignore */ };
            }
            const D = class { run() { /* @ts-ignore */ } };
            var v = function () { /* @ts-ignore */ };
            let o = { a: () => { /* @ts-ignore */ }, "b": function () { /* @ts-ignore */ } };
            function* gen() { /* @ts-ignore */ }
            export default () => { /* @ts-ignore */ };
            call({ m() { /* @ts-ignore */ } });
            (function () { /* @ts-ignore */ })();
            """
        self.assertEqual(
            [function for _, _, function in hits(source)],
            ["C.g", "C.s", "C.constructor", "C.#p", "C.q", "D.run", "v", "o.a", "o.b", "gen", "default", "", ""],
        )

    def test_comment_above_declaration(self) -> None:
        source = """\
            // @ts-ignore
            export function f() {}

            // @ts-ignore
            const g = () => 1;

            class C {
              // eslint-disable-next-line a
              // another comment
              @dec
              m() {}

              // @ts-ignore
              x = 1;
            }

            // @ts-ignore

            function h() {}
            """
        self.assertEqual([function for _, _, function in hits(source)], ["f", "g", "C.m", "", ""])


class ErrorTest(unittest.TestCase):
    def test_s23_syntax_error(self) -> None:
        with self.assertRaises(SourceError) as raised:
            hits("function f( {\n", "src/w.ts")
        self.assertIn("syntax error", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
