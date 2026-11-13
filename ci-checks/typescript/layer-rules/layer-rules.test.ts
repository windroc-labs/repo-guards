import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, it, type TestContext } from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("layer-rules.ts", import.meta.url));

const RULES = {
  forbidden: [
    { name: "a-not-b", severity: "error", from: { path: "^src/a" }, to: { path: "^src/b" } },
  ],
};
const CLEAN = { "src/b/b.ts": "export const b = 1;\n", "src/a/a.ts": "export const a = 2;\n" };
const VIOLATION = {
  ...CLEAN,
  "src/a/a.ts": 'import { b } from "../b/b.ts";\nexport const a = b;\n',
};
const SCRIPT_CONFIGS = [".js", ".cjs", ".mjs", ".ts", ".cts", ".mts"].map(
  (e) => `.dependency-cruiser${e}`,
);

function project(t: TestContext, files: Record<string, string | object>): string {
  const dir = mkdtempSync(path.join(tmpdir(), "layer-rules-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const [name, content] of Object.entries(files)) {
    const file = path.join(dir, name);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, typeof content === "string" ? content : JSON.stringify(content));
  }
  return dir;
}

function run(cwd: string): { status: number | null; out: string } {
  const done = spawnSync(process.execPath, [SCRIPT], { cwd, encoding: "utf8" });
  return { status: done.status, out: done.stdout + done.stderr };
}

// A module that leaves a file behind when it runs, so a test can tell it never ran.
const marking = (marker: string, exported: string): string =>
  `require("node:fs").writeFileSync(${JSON.stringify(marker)}, "ran");\nmodule.exports = ${exported};\n`;

describe("no config", () => {
  it("prints one line naming the tool and every file looked for, and passes", (t) => {
    const done = run(project(t, VIOLATION));
    assert.equal(done.status, 0, done.out);
    const lines = done.out.trim().split("\n");
    assert.equal(lines.length, 1, done.out);
    assert.match(lines[0] ?? "", /dependency-cruiser/);
    for (const name of [".dependency-cruiser.json", ...SCRIPT_CONFIGS])
      assert.ok(done.out.includes(name), name);
  });

  it("looks only in the current directory, not in parents or children", (t) => {
    const dir = project(t, {
      ...VIOLATION,
      ".dependency-cruiser.json": RULES,
      "sub/.dependency-cruiser.json": RULES,
      "sub/inner/.keep": "",
    });
    assert.equal(run(path.join(dir, "sub", "inner")).status, 0);
    const child = project(t, { ...VIOLATION, "sub/.dependency-cruiser.json": RULES });
    const done = run(child);
    assert.equal(done.status, 0, done.out);
    assert.match(done.out, /skip/i);
  });
});

describe("with .dependency-cruiser.json", () => {
  it("passes when no rule is broken", (t) => {
    const done = run(project(t, { ...CLEAN, ".dependency-cruiser.json": RULES }));
    assert.equal(done.status, 0, done.out);
  });

  it("fails and names the rule when one is broken", (t) => {
    const done = run(project(t, { ...VIOLATION, ".dependency-cruiser.json": RULES }));
    assert.equal(done.status, 1, done.out);
    assert.match(done.out, /a-not-b: src\/a\/a\.ts → src\/b\/b\.ts/);
  });

  it("fails on 256 violations, which dependency-cruiser alone would exit 0 on", (t) => {
    const files: Record<string, string | object> = { ...CLEAN, ".dependency-cruiser.json": RULES };
    for (let i = 0; i < 256; i++)
      files[`src/a/a${i}.ts`] = 'import { b } from "../b/b.ts";\nexport const a = b;\n';
    const done = run(project(t, files));
    assert.equal(done.status, 1, done.out);
    assert.match(done.out, /256 errors/);
  });

  it("checks the whole project, not only some directories", (t) => {
    const done = run(
      project(t, {
        "lib/core/c.ts": "export const c = 1;\n",
        "lib/ui/u.ts": 'import { c } from "../core/c.ts";\nexport const u = c;\n',
        ".dependency-cruiser.json": {
          forbidden: [
            {
              name: "ui-not-core",
              severity: "error",
              from: { path: "^lib/ui" },
              to: { path: "^lib/core" },
            },
          ],
        },
      }),
    );
    assert.equal(done.status, 1, done.out);
    assert.match(done.out, /ui-not-core/);
  });

  it("fails when dependency-cruiser rejects the config", (t) => {
    const done = run(
      project(t, {
        ...CLEAN,
        ".dependency-cruiser.json": { forbidden: [{ from: { path: "^src/a" } }] },
      }),
    );
    assert.notEqual(done.status, 0, done.out);
  });

  it("ignores the violations listed in .dependency-cruiser-known-violations.json", (t) => {
    const known = [
      {
        type: "dependency",
        from: "src/a/a.ts",
        to: "src/b/b.ts",
        rule: { severity: "error", name: "a-not-b" },
      },
    ];
    const done = run(
      project(t, {
        ...VIOLATION,
        ".dependency-cruiser.json": RULES,
        ".dependency-cruiser-known-violations.json": known,
      }),
    );
    assert.equal(done.status, 0, done.out);
  });

  it("still fails on a violation the known-violations file does not list", (t) => {
    const known = [
      {
        type: "dependency",
        from: "src/a/x.ts",
        to: "src/b/b.ts",
        rule: { severity: "error", name: "a-not-b" },
      },
    ];
    const done = run(
      project(t, {
        ...VIOLATION,
        ".dependency-cruiser.json": RULES,
        ".dependency-cruiser-known-violations.json": known,
      }),
    );
    assert.equal(done.status, 1, done.out);
  });

  it("uses the JSON config and never loads a script config next to it", (t) => {
    const dir = project(t, CLEAN);
    const marker = path.join(dir, "ran");
    writeFileSync(path.join(dir, ".dependency-cruiser.json"), JSON.stringify(RULES));
    writeFileSync(path.join(dir, ".dependency-cruiser.cjs"), marking(marker, "{ forbidden: [] }"));
    const done = run(dir);
    assert.equal(done.status, 0, done.out);
    assert.equal(existsSync(marker), false);
  });

  it("leaves no cache in the project", (t) => {
    const dir = project(t, {
      ...CLEAN,
      ".dependency-cruiser.json": { ...RULES, options: { cache: true } },
    });
    assert.equal(run(dir).status, 0);
    assert.equal(existsSync(path.join(dir, "node_modules")), false);
  });
});

describe("never runs the project's code", () => {
  for (const name of SCRIPT_CONFIGS) {
    it(`fails on a lone ${name} without loading it`, (t) => {
      const dir = project(t, VIOLATION);
      const marker = path.join(dir, "ran");
      const commonjs = [
        ".dependency-cruiser.js",
        ".dependency-cruiser.cjs",
        ".dependency-cruiser.cts",
      ].includes(name);
      const body = commonjs
        ? marking(marker, "{ forbidden: [] }")
        : `import { writeFileSync } from "node:fs";\nwriteFileSync(${JSON.stringify(marker)}, "ran");\nexport default { forbidden: [] };\n`;
      writeFileSync(path.join(dir, name), body);
      const done = run(dir);
      assert.equal(done.status, 1, done.out);
      assert.ok(done.out.includes(name), done.out);
      assert.match(done.out, /\.dependency-cruiser\.json/);
      assert.equal(existsSync(marker), false);
    });
  }

  for (const [label, config, file] of [
    ["an extends target", { ...RULES, extends: "./base.cjs" }, "base.cjs"],
    [
      "a webpack config",
      { ...RULES, options: { webpackConfig: { fileName: "webpack.config.cjs" } } },
      "webpack.config.cjs",
    ],
    ["an extends package", { ...RULES, extends: "pkg/rules" }, "node_modules/pkg/rules.cjs"],
  ] as const) {
    it(`fails on ${label} in JavaScript without loading it`, (t) => {
      const dir = project(t, { ...CLEAN, ".dependency-cruiser.json": config });
      const marker = path.join(dir, "ran");
      mkdirSync(path.dirname(path.join(dir, file)), { recursive: true });
      writeFileSync(path.join(dir, file), marking(marker, "{}"));
      const done = run(dir);
      assert.notEqual(done.status, 0, done.out);
      assert.equal(existsSync(marker), false, done.out);
    });
  }

  it("never loads a babel config in JavaScript: no babel is installed beside dependency-cruiser", (t) => {
    const dir = project(t, {
      ...CLEAN,
      ".dependency-cruiser.json": {
        ...RULES,
        options: { babelConfig: { fileName: "babel.config.cjs" } },
      },
    });
    const marker = path.join(dir, "ran");
    writeFileSync(path.join(dir, "babel.config.cjs"), marking(marker, "{}"));
    const done = run(dir);
    assert.equal(done.status, 0, done.out);
    assert.equal(existsSync(marker), false, done.out);
  });
});
