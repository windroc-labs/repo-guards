import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, it, type TestContext } from "node:test";
import {
  BudgetError,
  chars,
  CONFIG,
  glob,
  isTestPath,
  measure,
  parseArgs,
  parseConfig,
  PURPOSE,
  run,
  type Counts,
} from "./comment-budget.ts";

const DEFAULT = readFileSync(new URL("../comment-budget.toml", import.meta.url), "utf8");

const counts = (comment: string, code: string): Counts => ({
  comment: chars(comment),
  code: chars(code),
});

function source(text: string, file = "a.ts"): Counts {
  const m = measure(text, file, false);
  assert.deepEqual(m.tests, { comment: 0, code: 0 }, text);
  return m.source;
}

const errorOf = (f: () => unknown): string => {
  try {
    f();
  } catch (error) {
    assert.ok(error instanceof BudgetError, String(error));
    return error.message;
  }
  return assert.fail("expected a BudgetError");
};

describe("measuring", () => {
  it("counts line, block and JSDoc comments", () => {
    for (const [text, comment, code] of [
      ["let x = 1; // note\n", "// note", "let x = 1;"],
      ["/** Doc. */\nfunction f() {}\n", "/** Doc. */", "function f() {}"],
      ["a(/* x */ 1, /* y */ 2);", "/* x */ /* y */", "a(1, 2);"],
      ["const s = `a${b /* c */}d`;", "/* c */", "const s = `a${b}d`;"],
      ["class A {\n  // m\n  f(): void {}\n}\n// end\n", "// m // end", "class A { f(): void {} }"],
    ] as const) {
      assert.deepEqual(source(text), counts(comment, code), text);
    }
  });

  it("never counts strings, templates, regex literals or JSX text", () => {
    for (const [text, file] of [
      ['const s = "// not a comment /* either */";', "a.ts"],
      ["const s = 'escaped \\' // still a string';", "a.ts"],
      ["const s = `line ${x} // in a template /* too */`;", "a.ts"],
      ["const t = `${a}${`${b} // nested`}`;", "a.ts"],
      ["const r = /a\\/\\/b/g; const q = /[/]/;", "a.ts"],
      ["const r = x / 2 / y;", "a.ts"],
      ["const e = <a>// text /* too */</a>;", "a.tsx"],
      ['const e = <a href="// x">t</a>;', "a.jsx"],
      ["const s = '// js';", "a.mjs"],
    ] as const) {
      assert.deepEqual(source(text, file), counts("", text), text);
    }
  });

  it("counts comments inside empty blocks, literals and JSX expressions", () => {
    for (const [text, file] of [
      ["function f() { /* c */ }", "a.ts"],
      ["const o = { /* c */ }; const a = [/* c */];", "a.ts"],
      ["const e = <a>{/* c */}</a>;", "a.tsx"],
    ] as const) {
      const comment = "/* c */".repeat(text.split("/* c */").length - 1);
      assert.deepEqual(source(text, file), counts(comment, text.replaceAll("/* c */", "")));
    }
  });

  it("treats a first-line shebang as code", () => {
    const text = "#!/usr/bin/env node\nlet x = 1; // why\n";
    assert.deepEqual(source(text, "a.js"), counts("// why", "#!/usr/bin/env node let x = 1;"));
  });

  it("rejects unparsable files", () => {
    for (const [text, file] of [
      ["/* open", "a.ts"],
      ['"open', "a.ts"],
      ["`open ${x}", "a.ts"],
      ["let x = (", "a.ts"],
      ["let x: number = 1;", "a.js"],
    ] as const) {
      assert.match(
        errorOf(() => measure(text, file, false)),
        /^line 1: /,
        text,
      );
    }
  });

  it("counts the whole file as tests on a test path", () => {
    const m = measure("let x = 1; // a\n", "a.test.ts", true);
    assert.deepEqual([m.source, m.tests], [{ comment: 0, code: 0 }, counts("// a", "let x = 1;")]);
  });
});

describe("configuring", () => {
  it("parses the template config", () => {
    assert.deepEqual(parseConfig(DEFAULT), {
      source_percent: 10,
      test_percent: 5,
      allowance_chars: 300,
      test_paths: [
        "**/*.test.ts",
        "**/*.spec.ts",
        "**/*.test.tsx",
        "**/*.spec.tsx",
        "**/__tests__/**",
        "**/tests/**",
      ],
    });
  });

  it("names the problem in config errors", () => {
    const withText = (from: string, to: string) => DEFAULT.replace(from, to);
    for (const [text, error] of [
      [withText("test_percent = 5\n", ""), "missing key `test_percent`"],
      [withText("test_paths", "test_globs"), "unknown key `test_globs`"],
      [`${DEFAULT}test_percent = 1\n`, "line 5"],
      [withText("= 10", '= "10"'), "`source_percent` must be an integer"],
      [withText("= 10", "= -1"), "`source_percent` must be an integer"],
      [withText("= 10", "= true"), "`source_percent` must be an integer"],
      [withText("= 300", "= 1.5"), "`allowance_chars` must be an integer"],
      [withText("= 300", "= 1.0"), "`allowance_chars` must be an integer"],
      [withText("= 5", '= ["5"]'), "`test_percent` must be an integer"],
      [withText('["**/*.test.ts", ', "[1, "), "`test_paths` must be a list of strings"],
      [withText("= [", '= "tests/**" #['), "`test_paths` must be a list of strings"],
      ["[section]\n", "unknown key `section`"],
      ["x = \n", "line 1"],
    ] as const) {
      assert.ok(errorOf(() => parseConfig(text)).includes(error), text);
    }
  });

  it("crosses directories only with a double star", () => {
    const config = parseConfig(DEFAULT);
    for (const file of [
      "tests/a.ts",
      "pkg/tests/unit/c.ts",
      "src/__tests__/b.tsx",
      "a.test.ts",
      "src/a.spec.ts",
      "src/ui/a.test.tsx",
      "src/ui/a.spec.tsx",
    ]) {
      assert.ok(isTestPath(config, file), file);
    }
    for (const file of ["src/e.ts", "contests/f.ts", "tests.ts", "src/test.ts", "a.test.js"]) {
      assert.ok(!isTestPath(config, file), file);
    }
    assert.ok(glob("src/*.ts", "src/a.ts"));
    assert.ok(!glob("src/*.ts", "src/a/b.ts"));
    assert.ok(glob("a/**/b.ts", "a/b.ts"));
    assert.ok(glob("*é*", "xéy"));
  });

  it("has argument defaults and overrides", () => {
    assert.deepEqual(parseArgs([]), { base: "origin/main", config: undefined, all: false });
    assert.deepEqual(parseArgs(["--base", "HEAD", "--config", "c.toml", "--all"]), {
      base: "HEAD",
      config: "c.toml",
      all: true,
    });
    for (const bad of ["--base", "--config", "--source-limit 1", "--nope"]) {
      errorOf(() => parseArgs(bad.split(" ")));
    }
  });
});

class Repo {
  readonly root = mkdtempSync(path.join(tmpdir(), "comment-budget-"));

  constructor() {
    this.git("init", "-q", "-b", "main");
    this.write(CONFIG, DEFAULT);
  }

  git(...args: string[]): void {
    const done = spawnSync("git", [
      ...["-c", "user.name=t", "-c", "user.email=t@example.com"],
      ...["-c", "commit.gpgsign=false", "-C", this.root, ...args],
    ]);
    assert.equal(done.status, 0, `git ${args.join(" ")}`);
  }

  write(file: string, text: string): void {
    const full = path.join(this.root, file);
    mkdirSync(path.dirname(full), { recursive: true });
    writeFileSync(full, text);
  }

  run(args: string): [boolean, string] {
    const lines: string[] = [];
    const result = run(args.split(" ").filter(Boolean), this.root, (line) => lines.push(line));
    return [result, lines.map((line) => `${line}\n`).join("")];
  }
}

function repo(t: TestContext): Repo {
  const r = new Repo();
  t.after(() => rmSync(r.root, { recursive: true, force: true }));
  return r;
}

describe("running", () => {
  it("checks only files changed since the merge base", (t) => {
    const r = repo(t);
    r.write("src/old.ts", `// ${"x".repeat(400)}\nx = 1;\n`);
    r.git("add", ".");
    r.git("commit", "-q", "-m", "base");
    r.write("src/new.ts", `// ${"y".repeat(298)}\ny = 12;\n`);
    r.write("tests/new.ts", "t = 1;\n");
    r.write("src/types.d.ts", `// ${"z".repeat(400)}\n`);
    r.git("add", ".");
    let [result, out] = r.run("--base main");
    assert.ok(result, out);
    assert.ok(
      out.startsWith(
        "ok comment budget (source 10%, tests 5% of code + 300 characters " +
          "per changed file): 2 checked\n",
      ),
      out,
    );
    r.write("src/new.ts", `// ${"y".repeat(300)}\ny = 12;\n`);
    [result, out] = r.run("--base main");
    assert.ok(!result, out);
    assert.ok(out.includes("FAIL comment budget"), out);
    assert.ok(
      out.includes(
        "src/new.ts: 6040.00% (302 comment / 5 code characters, 0 in tests; " +
          "300 allowed, 2 over)",
      ),
      out,
    );
    assert.ok(out.trimEnd().endsWith(PURPOSE), out);
    assert.ok(!out.includes("src/old.ts:"), out);
  });

  it("checks every tracked file with --all and reports totals", (t) => {
    const r = repo(t);
    r.write("src/old.ts", `// ${"x".repeat(400)}\nx = 1;\n`);
    r.write("src/a.test.ts", "/** ab */\nt = 1;\n");
    r.write("notes.md", "// not code\n");
    r.git("add", ".");
    r.git("commit", "-q", "-m", "base");
    let [result, out] = r.run("--all");
    assert.ok(!result, out);
    assert.ok(out.includes("1 of 2 over budget"), out);
    assert.ok(out.includes("total source: 402 comment / 4 code characters (10050.00%)"), out);
    assert.ok(out.includes("total tests: 7 comment / 4 code characters (175.00%)"), out);
    [result, out] = r.run("--base main");
    assert.ok(result, out);
    assert.ok(out.includes(": 0 checked"), out);
  });

  it("lets the config path be overridden and requires it to exist", (t) => {
    const r = repo(t);
    r.write("a.ts", "x = 1;\n");
    r.write("strict.toml", DEFAULT.replace("= 300", "= 0"));
    r.git("add", "a.ts");
    r.git("commit", "-q", "-m", "base");
    const [result, out] = r.run("--all --config strict.toml");
    assert.ok(result, out);
    assert.ok(out.includes("+ 0 characters per file"), out);
    let error = errorOf(() => r.run("--all --config missing.toml"));
    assert.ok(error.startsWith("config ") && error.includes("missing.toml"), error);
    unlinkSync(path.join(r.root, CONFIG));
    error = errorOf(() => r.run("--all"));
    assert.ok(error.includes(CONFIG), error);
  });

  it("rejects a bad base or an unparsable file", (t) => {
    const r = repo(t);
    r.write("a.ts", "x = 1;\n");
    r.git("add", ".");
    r.git("commit", "-q", "-m", "base");
    errorOf(() => r.run("--base nope"));
    r.write("a.ts", "x = (\n");
    assert.ok(errorOf(() => r.run("--all")).startsWith("a.ts: "));
  });
});
