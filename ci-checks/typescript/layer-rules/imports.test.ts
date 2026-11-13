import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { makeHost, scanRequest, type Output, type Request } from "./imports.ts";

const SCRIPT = fileURLToPath(new URL("./imports.ts", import.meta.url));
const temps: string[] = [];
after(() => {
  for (const dir of temps) rmSync(dir, { recursive: true, force: true });
});

function tempDir(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "layer-rules-ts-"));
  temps.push(dir);
  return dir;
}

function write(root: string, files: Record<string, string | Uint8Array>): void {
  for (const [file, text] of Object.entries(files)) {
    const target = path.join(root, file);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, text);
  }
}

const isSource = (f: string): boolean => /\.[cm]?[jt]sx?$/.test(f);
const isContext = (f: string): boolean => /(^|\/)(tsconfig[^/]*\.json|package\.json)$/.test(f);

/** Writes a side root as the core does: the scanned files and the context files; `listed` adds source files that are named but not written. */
function scan(files: Record<string, string>, scanned: string[], listed: string[] = []): Output[] {
  const root = tempDir();
  write(root, files);
  const request: Request = {
    files: [...new Set([...Object.keys(files).filter(isSource), ...listed])].sort(),
    scan: scanned,
    context: Object.keys(files).filter(isContext).sort(),
    settings: { "source-dirs": ["src"] },
  };
  return scanRequest(root, request);
}

function targets(files: Record<string, string>, file = "src/a.ts", listed: string[] = []): string[][] {
  return scan(files, [file], listed).map((r) => {
    assert.ok("targets" in r, `unexpected record ${JSON.stringify(r)}`);
    return r.targets;
  });
}

describe("reference sites", () => {
  it("every static form is one site with its line; dynamic import is not", () => {
    const text = [
      'import { a, b } from "./x";',
      'import type { T } from "./x";',
      'import "./x";',
      'export { c } from "./x";',
      'export * from "./x";',
      'export * as ns from "./x";',
      'export type { U } from "./x";',
      'import eq = require("./x");',
      'const r = require("./x");',
      "const t = require(`./x`);",
      'type I = typeof import("./x");',
      'type J = import("./x").T;',
      'const d = await import("./x");',
      'const m = module.require("./x");',
      'const v = require("./" + "x");',
      'import {',
      '  e,',
      '} from "./x";',
      "function f() {",
      '  return require("./x");',
      "}",
      "",
    ].join("\n");
    const records = scan({ "src/a.ts": text, "src/x.ts": "" }, ["src/a.ts"]);
    assert.deepEqual(
      records.map((r) => ("line" in r ? r.line : r)),
      [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 20],
    );
    for (const r of records) assert.deepEqual("targets" in r && r.targets, ["src/x.ts"]);
  });

  it("drops the file itself, packages and unresolved specifiers; keeps duplicates as separate sites", () => {
    const text = 'import "./a";\nimport "react";\nimport "node:fs";\nimport "./missing";\nimport "./x";\nimport "./x";\n';
    const records = scan({ "src/a.ts": text, "src/x.ts": "" }, ["src/a.ts"]);
    assert.deepEqual(records, [
      { file: "src/a.ts", line: 5, targets: ["src/x.ts"] },
      { file: "src/a.ts", line: 6, targets: ["src/x.ts"] },
    ]);
  });

  it("tsx, jsx, mts, cts and js files are parsed", () => {
    const files = {
      "src/a.tsx": 'import "./x";\nconst e = <div>{1}</div>;\n',
      "src/b.jsx": 'import "./x";\nconst e = <div />;\n',
      "src/c.mts": 'import "./x.js";\n',
      "src/d.cts": 'import x = require("./x");\n',
      "src/e.js": 'const x = require("./x");\n',
      "src/x.ts": "",
    };
    for (const file of ["src/a.tsx", "src/b.jsx", "src/c.mts", "src/d.cts", "src/e.js"]) {
      assert.deepEqual(targets(files, file), [["src/x.ts"]], file);
    }
  });
});

describe("resolution", () => {
  it("relative: extensionless, index, .js to .ts, .ts extension, .d.ts, js sources", () => {
    const files = {
      "src/a.ts": [
        'import "./core";',
        'import "./b.js";',
        'import "./b.ts";',
        'import "./types";',
        'import "./plain";',
        'import "./esm.mjs";',
        "",
      ].join("\n"),
      "src/core/index.ts": "",
      "src/b.ts": "",
      "src/types.d.ts": "",
      "src/plain.js": "",
      "src/esm.mts": "",
    };
    assert.deepEqual(targets(files), [
      ["src/core/index.ts"],
      ["src/b.ts"],
      ["src/b.ts"],
      ["src/types.d.ts"],
      ["src/plain.js"],
      ["src/esm.mts"],
    ]);
  });

  it("names in files but not written still resolve", () => {
    assert.deepEqual(targets({ "src/a.ts": 'import "./x";\n' }, "src/a.ts", ["src/x.ts"]), [["src/x.ts"]]);
  });

  it("a file on disk but not in files is no target", () => {
    const root = tempDir();
    write(root, { "src/a.ts": 'import "./x";\n', "src/x.ts": "" });
    const request: Request = { files: ["src/a.ts"], scan: ["src/a.ts"], context: [], settings: {} };
    assert.deepEqual(scanRequest(root, request), []);
  });

  it("tsconfig paths with and without baseUrl", () => {
    const source = { "src/a.ts": 'import "@core/inner";\n', "src/core/inner.ts": "" };
    for (const options of [{ paths: { "@core/*": ["src/core/*"] } }, { baseUrl: "src", paths: { "@core/*": ["core/*"] } }]) {
      const files = { ...source, "tsconfig.json": JSON.stringify({ compilerOptions: options }) };
      assert.deepEqual(targets(files), [["src/core/inner.ts"]], JSON.stringify(options));
    }
  });

  it("extends a tracked tsconfig", () => {
    const files = {
      "src/a.ts": 'import "@core/inner";\n',
      "src/core/inner.ts": "",
      "tsconfig.base.json": JSON.stringify({ compilerOptions: { paths: { "@core/*": ["./src/core/*"] } } }),
      "tsconfig.json": JSON.stringify({ extends: "./tsconfig.base.json" }),
    };
    assert.deepEqual(targets(files), [["src/core/inner.ts"]]);
  });

  it("an extends of a package or a missing file does not stop resolution", () => {
    const files = {
      "src/a.ts": 'import "./x";\nimport "@core/x";\n',
      "src/x.ts": "",
      "tsconfig.json": JSON.stringify({
        extends: "@tsconfig/node22/tsconfig.json",
        compilerOptions: { paths: { "@core/*": ["./src/*"] }, plugins: [{ name: "evil-plugin" }] },
      }),
    };
    assert.deepEqual(targets(files), [["src/x.ts"], ["src/x.ts"]]);
  });

  it("the nearest tsconfig.json owns the file", () => {
    const files = {
      "tsconfig.json": JSON.stringify({ compilerOptions: { paths: { "@x/*": ["./other/*"] } } }),
      "pkg/tsconfig.json": JSON.stringify({ compilerOptions: { paths: { "@x/*": ["./src/*"] } } }),
      "pkg/src/a.ts": 'import "@x/b";\n',
      "pkg/src/b.ts": "",
      "other/b.ts": "",
    };
    assert.deepEqual(targets(files, "pkg/src/a.ts"), [["pkg/src/b.ts"]]);
  });

  it("a solution tsconfig hands the file to the referenced project that includes it", () => {
    const files = {
      "tsconfig.json": JSON.stringify({
        files: [],
        references: [{ path: "./tsconfig.node.json" }, { path: "./tsconfig.app.json" }],
      }),
      "tsconfig.node.json": JSON.stringify({ include: ["vite.config.ts"], compilerOptions: { composite: true } }),
      "tsconfig.app.json": JSON.stringify({
        include: ["src"],
        compilerOptions: { composite: true, paths: { "@/*": ["./src/*"] } },
      }),
      "src/a.ts": 'import "@/core/inner";\n',
      "src/core/inner.ts": "",
    };
    assert.deepEqual(targets(files), [["src/core/inner.ts"]]);
  });

  it("reference cycles end the walk", () => {
    const files = {
      "tsconfig.json": JSON.stringify({ files: [], references: [{ path: "./a" }] }),
      "a/tsconfig.json": JSON.stringify({ files: [], references: [{ path: "../b" }] }),
      "b/tsconfig.json": JSON.stringify({ files: [], references: [{ path: "../a" }, { path: ".." }] }),
      "src/a.ts": 'import "./x";\n',
      "src/x.ts": "",
    };
    assert.deepEqual(targets(files), [["src/x.ts"]]);
  });

  it("package.json imports under node16, nodenext and bundler", () => {
    for (const [module, moduleResolution] of [
      ["node16", "node16"],
      ["nodenext", "nodenext"],
      ["esnext", "bundler"],
    ]) {
      const files = {
        "package.json": JSON.stringify({ type: "module", imports: { "#core/*": "./src/core/*" } }),
        "tsconfig.json": JSON.stringify({ compilerOptions: { module, moduleResolution } }),
        "src/a.ts": 'import "#core/inner.js";\n',
        "src/core/inner.ts": "",
      };
      assert.deepEqual(targets(files), [["src/core/inner.ts"]], moduleResolution);
    }
  });

  it("require resolves in CommonJS mode under nodenext", () => {
    const files = {
      "tsconfig.json": JSON.stringify({ compilerOptions: { module: "nodenext", moduleResolution: "nodenext" } }),
      "src/a.cts": 'const x = require("./x.cjs");\n',
      "src/x.cts": "",
    };
    assert.deepEqual(targets(files, "src/a.cts"), [["src/x.cts"]]);
  });
});

describe("confinement", () => {
  it("ts.matchFiles is present on the pinned module", () => {
    assert.equal(typeof (ts as unknown as { matchFiles?: unknown }).matchFiles, "function");
  });

  it("host never reads outside the side root", () => {
    const parent = tempDir();
    write(parent, {
      "outside.ts": "",
      "outside.json": JSON.stringify({ compilerOptions: { paths: { "@x/*": ["./*"] } } }),
    });
    const root = path.join(parent, "side");
    const files = {
      "tsconfig.json": JSON.stringify({
        include: ["../**/*", "/**/*"],
        extends: "../outside.json",
        compilerOptions: { paths: { "@x/*": [`${parent}/*`, "../*"] } },
      }),
      "src/a.ts": 'import "@x/outside";\nimport "../outside";\nimport "../../outside";\n',
    };
    write(root, files);
    const request: Request = { files: ["src/a.ts"], scan: ["src/a.ts"], context: ["tsconfig.json"], settings: {} };
    assert.deepEqual(scanRequest(root, request), []);
    const host = makeHost(root, request);
    for (const outside of [path.join(parent, "outside.ts"), path.join(parent, "outside.json"), "/etc/passwd"]) {
      assert.equal(host.fileExists(outside), false, outside);
      assert.equal(host.readFile(outside), undefined, outside);
    }
    assert.equal(host.directoryExists?.(parent), false);
    assert.deepEqual(host.readDirectory(parent, [".ts"], undefined, ["**/*"]), []);
    for (const found of host.readDirectory(root, [".ts"], undefined, ["../**/*", "/**/*"])) {
      assert.ok(found.startsWith(`${root}/`), found);
    }
    assert.deepEqual(host.readDirectory(root, [".ts"], undefined, ["**/*"]), [path.join(root, "src/a.ts")]);
  });
});

describe("errors", () => {
  it("a syntax error is one error record", () => {
    assert.deepEqual(scan({ "src/a.ts": 'import "./x";\nconst = ;\n', "src/x.ts": "" }, ["src/a.ts"]), [
      { file: "src/a.ts", error: "line 2: Variable declaration expected." },
    ]);
  });

  it("invalid UTF-8 is one error record", () => {
    const root = tempDir();
    write(root, { "src/a.ts": new Uint8Array([0xff, 0xfe, 0x00]) });
    const request: Request = { files: ["src/a.ts"], scan: ["src/a.ts"], context: [], settings: {} };
    assert.deepEqual(scanRequest(root, request), [{ file: "src/a.ts", error: "not valid UTF-8" }]);
  });
});

describe("command line", () => {
  function run(args: string[], input: string): { status: number | null; stdout: string; stderr: string } {
    const result = spawnSync(process.execPath, [SCRIPT, ...args], { input, encoding: "utf8", cwd: "/" });
    return { status: result.status, stdout: result.stdout, stderr: result.stderr };
  }

  it("prints one JSON line per site", () => {
    const root = tempDir();
    write(root, { "src/a.ts": 'import "./x";\n' });
    const request = { files: ["src/a.ts", "src/x.ts"], scan: ["src/a.ts"], context: [], settings: {} };
    const result = run([root], JSON.stringify(request));
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '{"file":"src/a.ts","line":1,"targets":["src/x.ts"]}\n');
  });

  it("without typescript installed it exits 2 with one imports.ts: line", () => {
    // The scanner, unmodified, copied where no node_modules holds typescript.
    const copy = path.join(tempDir(), "imports.ts");
    writeFileSync(copy, readFileSync(SCRIPT));
    const result = spawnSync(process.execPath, [copy, tempDir()], { input: "{}", encoding: "utf8", cwd: "/" });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /^imports\.ts: typescript is not installed: run npm ci --ignore-scripts in .+\n$/);
  });

  it("bad usage and bad requests exit 2 with one imports.ts: line", () => {
    const root = tempDir();
    for (const [args, input] of [
      [[], "{}"],
      [[root], "not json"],
      [[root], JSON.stringify({ files: "x", scan: [], context: [] })],
      [[root], JSON.stringify({ files: ["../x.ts"], scan: [], context: [] })],
      [[root], JSON.stringify({ files: ["/x.ts"], scan: [], context: [] })],
      [[root], JSON.stringify({ files: ["src/a.ts"], scan: ["src/a.ts"], context: [] })],
    ] as const) {
      const result = run([...args], input);
      assert.equal(result.status, 2, `${JSON.stringify(args)} ${input}`);
      const lines = result.stderr.trim().split("\n");
      assert.match(lines.at(-1) ?? "", /^imports\.ts: /, result.stderr);
      assert.equal(lines.length, 1, result.stderr);
    }
  });
});
