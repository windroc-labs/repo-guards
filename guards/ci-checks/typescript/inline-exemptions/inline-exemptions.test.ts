import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { scanBytes, scanText, type Occurrence, type Output } from "./inline-exemptions.ts";

const SCRIPT = fileURLToPath(new URL("./inline-exemptions.ts", import.meta.url));

type Row = [tool: string, rule: string, fn: string];

function rows(text: string, file = "src/a.ts"): Row[] {
  return scanText(text, file).map((o) => {
    assert.ok("tool" in o, `unexpected error record ${JSON.stringify(o)}`);
    assert.equal(o.file, file);
    return [o.tool, o.rule, o.function];
  });
}

const inMethod = (line: string): string =>
  `class C {\n  run(ctx: any) {\n    const a = 1;\n    ${line}\n    return a;\n  }\n}\n`;

describe("TypeScript forms", () => {
  it("S-37 each form inside method run of class C", () => {
    for (const [line, tool, rule] of [
      ["// @ts-expect-error", "tsc", "ts-expect-error"],
      [
        "// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 说明",
        "eslint",
        "@typescript-eslint/no-explicit-any",
      ],
      ['/* eslint no-console: "off" */', "eslint", "no-console"],
      ["// prettier-ignore", "prettier", "ignore"],
      ["/* v8 ignore next */", "vitest-coverage", "next"],
      ["/* istanbul ignore if -- @preserve */", "vitest-coverage", "if"],
      ["/** @public */", "knip", "public"],
      ["ctx.skip()", "vitest", "skip"],
    ] as const) {
      assert.deepEqual(rows(inMethod(line)), [[tool, rule, "C.run"]], line);
    }
  });

  it("tsc directives: ignore, nocheck, JSDoc star lines, triple slash", () => {
    assert.deepEqual(rows("// @ts-ignore\nlet x = 1;\n"), [["tsc", "ts-ignore", ""]]);
    assert.deepEqual(rows("/// @ts-ignore\nlet x = 1;\n"), [["tsc", "ts-ignore", ""]]);
    assert.deepEqual(rows("/**\n * @ts-expect-error\n */\nlet x = 1;\n"), [
      ["tsc", "ts-expect-error", ""],
    ]);
    assert.deepEqual(rows("// @ts-nocheck\nlet x = 1;\n"), [["tsc", "ts-nocheck", ""]]);
    assert.deepEqual(rows("// see @ts-ignore docs\nlet x = 1;\n"), []);
  });

  it("eslint-disable forms, rule lists and descriptions", () => {
    assert.deepEqual(rows("/* eslint-disable */\nlet x = 1;\n"), [["eslint", "*", ""]]);
    assert.deepEqual(rows("// eslint-disable-next-line\nlet x = 1;\n"), [["eslint", "*", ""]]);
    assert.deepEqual(rows("let x = 1; // eslint-disable-line no-var, eqeqeq\n"), [
      ["eslint", "no-var", ""],
      ["eslint", "eqeqeq", ""],
    ]);
    assert.deepEqual(rows("let x = 1; /* eslint-disable-line no-var -- legacy, old */\n"), [
      ["eslint", "no-var", ""],
    ]);
    assert.deepEqual(rows("// eslint-disable-next-line -- why\nlet x = 1;\n"), [
      ["eslint", "*", ""],
    ]);
    assert.deepEqual(rows("/* eslint-enable */\n/* eslint-env node */\nlet x = 1;\n"), []);
    assert.deepEqual(rows("// eslint-disabled here\nlet x = 1;\n"), []);
  });

  it('eslint config comments count only rules set to off: /* eslint a: 0, b: "error" */', () => {
    assert.deepEqual(rows('/* eslint a: 0, b: "error" */\nlet x = 1;\n'), [["eslint", "a", ""]]);
    assert.deepEqual(
      rows(`/* eslint a: 'off', b: ["off", { x: 1 }], c: [0], d: ["error", "off"], e: 2 */\n`),
      [
        ["eslint", "a", ""],
        ["eslint", "b", ""],
        ["eslint", "c", ""],
      ],
    );
    assert.deepEqual(rows('// eslint no-console: "off"\nlet x = 1;\n'), []);
  });

  it("prettier-ignore in JSX braces is a real comment", () => {
    const text = "const e = (\n  <div>\n    {/* prettier-ignore */}\n    <b a='1'   />\n  </div>\n);\n";
    assert.deepEqual(rows(text, "src/a.tsx"), [["prettier", "ignore", ""]]);
  });

  it("coverage keywords, counts and stop", () => {
    assert.deepEqual(
      rows(
        "/* c8 ignore next 3 */\n/* v8 ignore start */\n/* v8 ignore stop */\n" +
          "/* istanbul ignore else */\n/* istanbul ignore file -- @preserve */\nlet x = 1;\n",
      ),
      [
        ["vitest-coverage", "next", ""],
        ["vitest-coverage", "start", ""],
        ["vitest-coverage", "else", ""],
        ["vitest-coverage", "file", ""],
      ],
    );
  });

  it("knip @public only in JSDoc", () => {
    assert.deepEqual(rows("/** Docs.\n * @public\n */\nexport const x = 1;\n"), [
      ["knip", "public", ""],
    ]);
    assert.deepEqual(rows("/* @public */\nexport const x = 1;\n"), []);
    assert.deepEqual(rows("/** @publicApi */\nexport const x = 1;\n"), []);
  });

  it("S-10 jscpd ignore-start counts once in function f, ignore-end never", () => {
    const text = "function f() {\n  // jscpd:ignore-start\n  g();\n  // jscpd:ignore-end\n}\n";
    assert.deepEqual(scanText(text, "src/a.ts"), [
      { tool: "jscpd", file: "src/a.ts", rule: "ignore", function: "f" },
    ]);
  });

  it("S-44 generated header in src/gen.ts", () => {
    assert.deepEqual(scanText("// @generated\nexport const x = 1;\n", "src/gen.ts"), [
      { tool: "generated", file: "src/gen.ts", rule: "generated", function: "" },
    ]);
  });

  it("generated header: shebang and blank lines allowed, other forms, once, top only", () => {
    assert.deepEqual(rows("#!/usr/bin/env node\n\n// Code generated by x. DO NOT EDIT.\nf();\n"), [
      ["generated", "generated", ""],
    ]);
    assert.deepEqual(
      rows("// Generated by the protocol buffer compiler.  DO NOT EDIT!\n/* @generated */\nf();\n"),
      [["generated", "generated", ""]],
    );
    assert.deepEqual(rows("f();\n// @generated\n"), []);
  });
});

describe("only real comments (FR-31)", () => {
  it("S-29 src/b.ts template string with // @ts-ignore yields nothing", () => {
    assert.deepEqual(rows("const s = `\n// @ts-ignore\n`;\n", "src/b.ts"), []);
  });

  it("strings, templates, regex literals and JSX text never count", () => {
    for (const [text, file] of [
      ['const s = "// @ts-ignore";', "a.ts"],
      ["const s = `${a} /* eslint-disable */ ${b}`;", "a.ts"],
      ["const t = `${a}${`// prettier-ignore`}`;", "a.ts"],
      ["const r = /\\/\\/ @ts-ignore/; const q = /\\/* c8 ignore next *\\//;", "a.ts"],
      ["const e = <a>// @ts-ignore\n/* jscpd:ignore-start */</a>;", "a.tsx"],
      ["const e = <a><b/>// eslint-disable-line\n</a>;", "a.jsx"],
      ["const e = <a>{x}// @ts-ignore\n</a>;", "a.tsx"],
      ['const s = "// @generated";', "a.ts"],
    ] as const) {
      assert.deepEqual(rows(text, file), [], text);
    }
  });

  it("comments inside template expressions and JSX expressions do count", () => {
    assert.deepEqual(rows("const s = `${/* @ts-ignore */ x}`;\n"), [["tsc", "ts-ignore", ""]]);
    assert.deepEqual(rows("const e = <a>{/* jscpd:ignore-start */}</a>;\n", "a.tsx"), [
      ["jscpd", "ignore", ""],
    ]);
  });

  it("S-30 types/y.d.ts with // @ts-ignore yields one", () => {
    assert.deepEqual(scanText("// @ts-ignore\ndeclare const x: number;\n", "types/y.d.ts"), [
      { tool: "tsc", file: "types/y.d.ts", rule: "ts-ignore", function: "" },
    ]);
  });

  it("every extension parses with its script kind", () => {
    for (const file of ["a.ts", "a.mts", "a.cts", "a.js", "a.mjs", "a.cjs"]) {
      assert.deepEqual(rows("// @ts-ignore\nlet x = 1;\n", file), [["tsc", "ts-ignore", ""]], file);
    }
    for (const file of ["a.tsx", "a.jsx"]) {
      assert.deepEqual(rows("// @ts-ignore\nconst e = <a>t</a>;\n", file), [["tsc", "ts-ignore", ""]]);
    }
    assert.ok("error" in (scanText("const e = <a>t</a>;\n", "a.ts")[0] ?? {}));
  });
});

describe("test markers", () => {
  it("S-38 markers in a top-level describe callback have function ''", () => {
    for (const [line, rule] of [
      ['it.skip("x", () => {})', "skip"],
      ['test.concurrent.skip("x", f)', "skip"],
      ['it.todo("x")', "todo"],
      ['it.fails("x", f)', "fails"],
      ['describe.skipIf(isWin)("x", f)', "skipIf"],
      ['xit("x", f)', "xit"],
    ] as const) {
      const text = `describe("d", () => {\n  ${line};\n});\n`;
      assert.deepEqual(
        scanText(text, "src/a.test.ts"),
        [{ tool: "vitest", file: "src/a.test.ts", rule, function: "" }],
        line,
      );
    }
  });

  it('S-28 import { it as t } from "vitest" then t.skip("x", f)', () => {
    const text = 'import { it as t } from "vitest";\nt.skip("x", f);\n';
    assert.deepEqual(rows(text, "src/a.test.ts"), [["vitest", "skip", ""]]);
  });

  it("S-14 it.only is not an exemption", () => {
    assert.deepEqual(scanText('it.only("x", f);\n', "src/a.test.ts"), []);
  });

  it(".skip.each counts once; other roots and markers", () => {
    assert.deepEqual(rows('test.skip.each([1, 2])("x %i", f);\n'), [["vitest", "skip", ""]]);
    assert.deepEqual(rows("test.skip.each`a | b`('x', f);\n"), [["vitest", "skip", ""]]);
    assert.deepEqual(rows('suite.runIf(ok)("x", f);\nxdescribe.each([1])("x", f);\n'), [
      ["vitest", "runIf", ""],
      ["vitest", "xdescribe", ""],
    ]);
    assert.deepEqual(rows('xtest("x", f);\nfoo.skip("x");\nit.each([1])("x", f);\n'), [
      ["vitest", "xtest", ""],
    ]);
  });

  it("namespace import of vitest", () => {
    const text = 'import * as v from "vitest";\nv.it.skip("x", f);\nv.describe.todo("y");\nv.skip();\n';
    assert.deepEqual(rows(text), [
      ["vitest", "skip", ""],
      ["vitest", "todo", ""],
    ]);
  });

  it("ctx.skip and context.skip in a test callback", () => {
    const text =
      'test("x", (ctx) => {\n  ctx.skip();\n});\nit("y", async (context) => {\n  context.skip(true, "n");\n});\n';
    assert.deepEqual(rows(text), [
      ["vitest", "skip", ""],
      ["vitest", "skip", ""],
    ]);
  });

  it("markers in a named function take its name", () => {
    assert.deepEqual(rows('function suiteA() {\n  it.skip("x", f);\n}\n'), [
      ["vitest", "skip", "suiteA"],
    ]);
  });
});

describe("function naming (FR-40)", () => {
  it("S-39 // @ts-ignore in each position", () => {
    for (const [text, fn] of [
      ["function f() {\n  // @ts-ignore\n  g();\n}\n", "f"],
      ["const g = () => {\n  // @ts-ignore\n  h();\n};\n", "g"],
      ["class C {\n  h = () => {\n    // @ts-ignore\n    k();\n  };\n}\n", "C.h"],
      ["const o = {\n  m() {\n    // @ts-ignore\n    k();\n  },\n};\n", "o.m"],
      ["export default function () {\n  // @ts-ignore\n  k();\n}\n", "default"],
      ["function f() {\n  [1].map(() => {\n    // @ts-ignore\n    k();\n  });\n}\n", "f"],
    ] as const) {
      assert.deepEqual(rows(text), [["tsc", "ts-ignore", fn]], text);
    }
  });

  it("class members, function expressions, default arrows and fallbacks", () => {
    const at = (text: string): string => {
      const found = rows(text);
      assert.equal(found.length, 1, text);
      return found[0]?.[2] ?? "";
    };
    const cases: [string, string][] = [
      ["class C {\n  constructor() {\n    // @ts-ignore\n  }\n}\n", "C.constructor"],
      ["class C {\n  get v() {\n    // @ts-ignore\n    return 1;\n  }\n}\n", "C.v"],
      ["class C {\n  set v(x) {\n    // @ts-ignore\n  }\n}\n", "C.v"],
      ["class C {\n  static s() {\n    // @ts-ignore\n  }\n}\n", "C.s"],
      ["class C {\n  #p() {\n    // @ts-ignore\n  }\n}\n", "C.#p"],
      ["class C {\n  f = function () {\n    // @ts-ignore\n  };\n}\n", "C.f"],
      ["const D = class {\n  m() {\n    // @ts-ignore\n  }\n};\n", "D.m"],
      ["class A {\n  m() {\n    class B {\n      n() {\n        // @ts-ignore\n      }\n    }\n  }\n}\n", "B.n"],
      ["let f = function () {\n  // @ts-ignore\n};\n", "f"],
      ["var o = {\n  p: () => {\n    // @ts-ignore\n  },\n};\n", "o.p"],
      ["export default () => {\n  // @ts-ignore\n};\n", "default"],
      ["[1].map(function named() {\n  // @ts-ignore\n});\n", "named"],
      ["(() => {\n  // @ts-ignore\n})();\n", ""],
      ["function f() {\n  (function () {\n    // @ts-ignore\n  })();\n}\n", "f"],
      ["function f() {\n  g({\n    m() {\n      // @ts-ignore\n    },\n  });\n}\n", "f"],
      ["class C {\n  // @ts-ignore\n  x: number = 1;\n}\n", ""],
      ["function f() {}\n// @ts-ignore\nconst y = 1;\n", ""],
    ];
    for (const [text, fn] of cases) assert.equal(at(text), fn, text);
  });

  it("comment-only run directly above a declaration attaches; a blank line breaks it", () => {
    assert.deepEqual(rows("// note\n// @ts-ignore\nfunction f() {}\n"), [["tsc", "ts-ignore", "f"]]);
    assert.deepEqual(rows("/** @public */\nexport const g = () => 1;\n"), [["knip", "public", "g"]]);
    assert.deepEqual(rows("class C {\n  /** @public */\n  m() {}\n}\n"), [["knip", "public", "C.m"]]);
    assert.deepEqual(rows("// @ts-ignore\n\nfunction f() {}\n"), [["tsc", "ts-ignore", ""]]);
    assert.deepEqual(rows("// @ts-ignore\n// note\n\nfunction f() {}\n"), [["tsc", "ts-ignore", ""]]);
    assert.deepEqual(rows("x(); // @ts-ignore\nfunction f() {}\n"), [["tsc", "ts-ignore", ""]]);
  });

  it("file-wide forms (@ts-nocheck, ignore file) have function '' even above a function", () => {
    assert.deepEqual(rows("// @ts-nocheck\n/* istanbul ignore file */\nfunction f() {}\n"), [
      ["tsc", "ts-nocheck", ""],
      ["vitest-coverage", "file", ""],
    ]);
  });
});

describe("errors", () => {
  it("S-23 src/w.ts with a syntax error gives an error record naming the file", () => {
    const [record, ...rest] = scanText("function (\n", "src/w.ts");
    assert.deepEqual(rest, []);
    assert.ok(record !== undefined && "error" in record, JSON.stringify(record));
    assert.equal(record.file, "src/w.ts");
    assert.match(record.error, /^line 1: /);
  });

  it("non-UTF-8 bytes give an error record", () => {
    const [record] = scanBytes(Uint8Array.from([0x2f, 0x2f, 0xff, 0x0a]), "src/v.ts");
    assert.ok(record !== undefined && "error" in record);
    assert.equal(record.file, "src/v.ts");
  });
});

describe("protocol", () => {
  const run = (root: string, input: string) =>
    spawnSync(process.execPath, [SCRIPT, root], {
      input,
      encoding: "utf8",
      cwd: path.dirname(SCRIPT),
    });

  it("reads NUL-separated paths, prints JSON lines, keeps going after errors, exits 0", () => {
    const root = mkdtempSync(path.join(tmpdir(), "inline-exemptions-"));
    try {
      mkdirSync(path.join(root, "src"));
      writeFileSync(path.join(root, "src/a.ts"), "function f() {\n  // @ts-ignore\n}\n");
      writeFileSync(path.join(root, "src/w.ts"), "function (\n");
      writeFileSync(path.join(root, "src/b.tsx"), "// eslint-disable-next-line a, b\nx();\n");
      const done = run(root, "src/a.ts\0src/w.ts\0src/b.tsx\0");
      assert.equal(done.status, 0, done.stderr);
      const lines = done.stdout.trimEnd().split("\n");
      const parsed = lines.map((l) => JSON.parse(l) as Output);
      assert.deepEqual(parsed[0], { tool: "tsc", file: "src/a.ts", rule: "ts-ignore", function: "f" });
      assert.equal(lines[0], '{"tool":"tsc","file":"src/a.ts","rule":"ts-ignore","function":"f"}');
      assert.deepEqual(Object.keys(parsed[1] ?? {}), ["file", "error"]);
      assert.equal(parsed[1]?.file, "src/w.ts");
      assert.deepEqual(parsed.slice(2) as Occurrence[], [
        { tool: "eslint", file: "src/b.tsx", rule: "a", function: "" },
        { tool: "eslint", file: "src/b.tsx", rule: "b", function: "" },
      ]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("empty input prints nothing and exits 0", () => {
    const done = run(tmpdir(), "");
    assert.equal(done.status, 0, done.stderr);
    assert.equal(done.stdout, "");
  });

  it("S-34 a package.json postinstall script (and scanned code) never runs", () => {
    const root = mkdtempSync(path.join(tmpdir(), "inline-exemptions-"));
    const marker = path.join(root, "pwned");
    try {
      const write = `require("fs").writeFileSync(${JSON.stringify(marker)}, "x")`;
      writeFileSync(
        path.join(root, "package.json"),
        JSON.stringify({ name: "x", scripts: { postinstall: `node -e '${write}'` } }),
      );
      writeFileSync(
        path.join(root, "index.ts"),
        `import { writeFileSync } from "node:fs";\nwriteFileSync(${JSON.stringify(marker)}, "x");\n`,
      );
      const done = run(root, "index.ts\0");
      assert.equal(done.status, 0, done.stderr);
      assert.equal(done.stdout, "");
      assert.equal(existsSync(marker), false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
