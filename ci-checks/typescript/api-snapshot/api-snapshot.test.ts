import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { Failure, generate, readScope, type Entry } from "./api-snapshot.ts";

const SCRIPT = fileURLToPath(new URL("./api-snapshot.ts", import.meta.url));
const TESTDATA = fileURLToPath(new URL("./testdata/", import.meta.url));
const scratch = mkdtempSync(path.join(tmpdir(), "api-snapshot-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));

let copies = 0;
function copy(fixture: string): string {
  const dir = path.join(scratch, `copy-${copies++}`, fixture);
  cpSync(path.join(TESTDATA, fixture), dir, { recursive: true, verbatimSymlinks: true });
  return dir;
}

function edit(file: string, from: string, to: string): void {
  const text = readFileSync(file, "utf8");
  assert.ok(text.includes(from), `${file} lacks ${from}`);
  writeFileSync(file, text.replace(from, to));
}

const keyOf = (e: Entry): string => JSON.stringify([e.unit, e.parent, e.kind, e.name]);

function find(entries: Entry[], unit: string, parent: string, kind: string, name: string): Entry {
  const found = entries.filter(
    (e) => e.unit === unit && e.parent === parent && e.kind === kind && e.name === name,
  );
  assert.equal(
    found.length,
    1,
    `expected one ${JSON.stringify([unit, parent, kind, name])} in ${JSON.stringify(entries, null, 1)}`,
  );
  return found[0]!;
}

function run(
  project: string,
  out: string,
  flags: string[] = [],
): { status: number | null; stdout: string; stderr: string } {
  const result = spawnSync(process.execPath, [SCRIPT, ...flags, project, out], {
    encoding: "utf8",
  });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

function listing(dir: string): string[] {
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .map((d) => path.relative(dir, path.join(d.parentPath, d.name)))
    .sort();
}

describe("generate on the basic fixture", () => {
  const entries = generate(path.join(TESTDATA, "basic"));
  const sig = (parent: string, kind: string, name: string, unit = "basic"): string =>
    find(entries, unit, parent, kind, name).signature;

  it("S-4 export function open is an entry with all five fields; function helper is not", () => {
    const open = find(entries, "basic", "", "function", "open");
    assert.deepEqual(Object.keys(open), ["unit", "parent", "kind", "name", "signature"]);
    assert.equal(open.signature, "function open(path: string, x: number = 5): number");
    assert.ok(!entries.some((e) => e.name === "helper" || e.signature.includes("helper")));
  });

  it("S-4 entries are sorted by unit, parent, kind, name and every key is unique", () => {
    const keys = entries.map((e) => [e.unit, e.parent, e.kind, e.name]);
    const byKey = (a: string[], b: string[]): number => {
      const i = a.findIndex((field, n) => field !== b[n]);
      return i === -1 ? 0 : a[i]! < b[i]! ? -1 : 1;
    };
    assert.deepEqual(keys, [...keys].sort(byKey));
    assert.equal(new Set(entries.map(keyOf)).size, entries.length);
  });

  it("S-24 the two overloads of load are one entry, in declaration order", () => {
    assert.equal(
      sig("", "function", "load"),
      "function load(id: number): string\nfunction load(id: string, strict?: boolean): string",
    );
  });

  it("top-level declarations: const with an arrow default, default export, type, enum", () => {
    assert.equal(sig("", "const", "twice"), "const twice: (n: number, by: number = 2) => number");
    assert.equal(sig("", "function", "default"), "function main(): void");
    assert.equal(sig("", "const", "VERSION"), 'const VERSION = "1.0"');
    assert.equal(sig("", "type", "Pair"), "type Pair<A> = [A, A]");
    assert.equal(sig("", "enum", "Color"), 'enum Color { Red = 0, Green = "g" }');
  });

  it("class members: public and protected in, private and #private out, static apart", () => {
    assert.equal(sig("", "class", "Box"), "class Box<T extends object = object> extends Base");
    assert.equal(
      sig("Box", "constructor", "constructor"),
      "constructor(value: T, scale: number = 2)",
    );
    assert.equal(sig("Box", "property", "value"), "readonly value: T");
    assert.equal(sig("Box", "property", "guard"), "protected guard: number");
    assert.equal(sig("Box", "static property", "count"), "static count: number");
    assert.equal(sig("Box", "get", "size"), "get size(): number");
    assert.equal(sig("Box", "set", "size"), "set size(v: number)");
    assert.equal(sig("Box", "method", "grow"), "grow(by: number = 1): this");
    assert.equal(sig("Box", "static method", "create"), "static create(): Box<object>");
    assert.equal(sig("Box", "method", "create"), "create(): void");
    const members = entries.filter((e) => e.parent === "Box").map((e) => e.name);
    assert.ok(
      !members.includes("hidden") && !members.some((m) => m.startsWith("#")),
      String(members),
    );
  });

  it("interface members, call and index signatures", () => {
    assert.equal(sig("", "interface", "Options"), "interface Options");
    assert.equal(sig("Options", "property", "size"), "readonly size?: number");
    assert.equal(sig("Options", "property", "label"), "label: string");
    assert.equal(sig("Options", "call", ""), "(x: number): void");
    assert.equal(sig("Options", "index", ""), "[key: string]: unknown");
  });

  it("namespace re-export and nested namespaces nest their parents", () => {
    assert.equal(sig("", "namespace", "util"), "namespace util");
    assert.equal(
      sig("util", "function", "clamp"),
      "function clamp(n: number, lo: number = 0, hi: number = 1): number",
    );
    assert.equal(sig("util", "namespace", "Shapes"), "namespace Shapes");
    assert.equal(sig("util.Shapes", "function", "origin"), "function origin(): Point");
    assert.equal(sig("util.Shapes.Point", "property", "x"), "x: number");
  });

  it("each exports subpath is a unit; null subpaths and packages without exports or types are not", () => {
    assert.equal(sig("", "let", "counter", "basic/extra"), "let counter: number");
    const units = [...new Set(entries.map((e) => e.unit))];
    assert.deepEqual(units, ["basic", "basic/extra"]);
  });
});

describe("exports forms", () => {
  it("condition arrays, subpath patterns and hand-written types; a nameless package uses its directory", () => {
    const entries = generate(path.join(TESTDATA, "conditions"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.kind, e.name, e.signature]),
      [
        [".", "const", "main", "const main = 1"],
        ["./features/a", "function", "a", "function a(): void"],
        ["./features/b", "function", "b", "function b(): void"],
        ["./hand", "function", "hand", "function hand(x: number): string"],
      ],
    );
  });

  it("Node's subpath rules: exact keys beat patterns, null patterns exclude, targets without types are skipped", () => {
    const entries = generate(path.join(TESTDATA, "exportsmap"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.name]),
      [
        ["em", "index"],
        ["em/a", "a"],
        ["em/b", "b"],
        ["em/index", "index"],
      ],
    );
  });

  it("a project importing .ts extensions builds, with defaults", () => {
    const entries = generate(path.join(TESTDATA, "tsext"));
    assert.deepEqual(entries, [
      {
        unit: "tsext",
        parent: "",
        kind: "function",
        name: "open",
        signature: "function open(x: number = 5): number",
      },
    ]);
  });

  it("an extensionless pattern target expands to each built module", () => {
    const entries = generate(path.join(TESTDATA, "patterns"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.name]),
      [
        ["q", "index"],
        ["q/a.js", "a"],
        ["q/index.js", "index"],
      ],
    );
  });

  it("a non-relative .ts import through package imports builds", () => {
    const entries = generate(path.join(TESTDATA, "tsimports"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.signature]),
      [["p", "function open(x: number = 5): number"]],
    );
  });

  it("a re-export through tsconfig paths to another project's source reads that project's .d.ts", () => {
    const entries = generate(path.join(TESTDATA, "paths"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.kind, e.name, e.signature]),
      [
        ["@x/app", "const", "y", "const y: () => number"],
        ["@x/app", "function", "coreFn", "function coreFn(a: number = 1): number"],
        ["@x/core", "function", "coreFn", "function coreFn(a: number = 1): number"],
      ],
    );
  });

  it("imports of ambient modules, exact and wildcard, resolve as they do for tsc", () => {
    const entries = generate(path.join(TESTDATA, "ambient"));
    assert.deepEqual(
      entries.map((e) => [e.kind, e.name, e.signature]),
      [
        ["const", "logo", "const url: string"],
        ["const", "other", "const value: number"],
        ["interface", "Cfg", "interface Cfg"],
        ["property", "a", "a: number"],
      ],
    );
  });

  it("names from an untyped JS module are any aliases, as tsc types them: directly, imported then exported, and through 2 and 3 hops, export = and import-equals aliases", () => {
    const entries = generate(path.join(TESTDATA, "untyped"));
    assert.deepEqual(
      entries.map((e) => [e.kind, e.name, e.signature]),
      [
        ["alias", "bar", "any"],
        ["alias", "default", "any"],
        ["alias", "foo", "any"],
        ["alias", "hop", "any"],
        ["alias", "viaMid", "any"],
        ["alias", "viaMid2", "any"],
        ["alias", "x", "any"],
        ["alias", "y", "any"],
        ["const", "own", "const own = 1"],
      ],
    );
  });

  it("a declaration file the units never reach is not checked, as tsc skips it", () => {
    const entries = generate(path.join(TESTDATA, "unrelated"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.signature]),
      [["unrelated", "const v = 1"]],
    );
  });

  it("a solution tsconfig builds its referenced projects", () => {
    const entries = generate(path.join(TESTDATA, "solution"));
    assert.deepEqual(
      entries.map((e) => [e.unit, e.signature]),
      [["solution", "function open(x: number = 5): number"]],
    );
  });

  it("workspace packages build against a referenced project's source", () => {
    const entries = generate(path.join(TESTDATA, "workspace"));
    assert.equal(
      find(entries, "@ws/app", "", "function", "make").signature,
      "function make(): Core",
    );
    assert.equal(find(entries, "@ws/app", "", "class", "Core").signature, "class Core");
    assert.equal(find(entries, "@ws/app", "Core", "method", "id").signature, "id(): string");
    assert.equal(find(entries, "@ws/core", "", "class", "Core").signature, "class Core");
  });
});

describe("signature changes", () => {
  const base = generate(path.join(TESTDATA, "basic"));
  const changedKeys = (after: Entry[]): string[] => {
    const before = new Map(base.map((e) => [keyOf(e), e.signature]));
    assert.deepEqual(after.map(keyOf), base.map(keyOf));
    return after.filter((e) => before.get(keyOf(e)) !== e.signature).map((e) => e.name);
  };

  it("S-6 changing the default of x from 5 to 6 changes the signature of open", () => {
    const dir = copy("basic");
    edit(path.join(dir, "src/index.ts"), "x = 5", "x = 6");
    const after = generate(dir);
    assert.deepEqual(changedKeys(after), ["open"]);
    assert.equal(
      find(after, "basic", "", "function", "open").signature,
      "function open(path: string, x: number = 6): number",
    );
  });

  it("S-24 changing one overload of load changes only the load entry", () => {
    const dir = copy("basic");
    edit(path.join(dir, "src/index.ts"), "strict?: boolean", "strict?: number");
    const after = generate(dir);
    assert.equal(new Set(after.map(keyOf)).size, after.length);
    assert.deepEqual(changedKeys(after), ["load"]);
  });

  it("S-7 editing the doc comment and moving open to another file of the unit changes nothing", () => {
    const dir = copy("basic");
    const index = path.join(dir, "src/index.ts");
    const text = readFileSync(index, "utf8");
    const moved = text.slice(0, text.indexOf("export function load"));
    assert.ok(moved.includes("function open") && moved.includes("function helper"));
    writeFileSync(index, `export { open } from "./open.js";\n\n${text.slice(moved.length)}`);
    writeFileSync(
      path.join(dir, "src/open.ts"),
      moved.replace("/** Opens a path. */", "/**\n * Opens the given path.\n */"),
    );
    assert.deepEqual(generate(dir), base);
  });
});

describe("command line", () => {
  it("S-8 two runs write byte-identical entry files with no absolute paths", () => {
    const dir = copy("basic");
    const out = mkdtempSync(path.join(scratch, "out-"));
    for (const name of ["a.json", "b.json"]) {
      const result = run(dir, path.join(out, name));
      assert.equal(result.status, 0, result.stderr);
    }
    const a = readFileSync(path.join(out, "a.json"), "utf8");
    assert.equal(a, readFileSync(path.join(out, "b.json"), "utf8"));
    assert.ok(!a.includes(scratch) && !a.includes(TESTDATA), "entries carry an absolute path");
    assert.deepEqual(JSON.parse(a), generate(dir));
    assert.ok(a.endsWith("]\n"));
  });

  it("security#5 a tsconfig pointing every output outside the project writes no file anywhere", () => {
    const dir = copy("hostile");
    const parent = path.dirname(dir);
    const before = listing(parent);
    const out = path.join(mkdtempSync(path.join(scratch, "out-")), "entries.json");
    const result = run(dir, out);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(listing(parent), before);
    assert.deepEqual(JSON.parse(readFileSync(out, "utf8")), [
      {
        unit: "hostile",
        parent: "",
        kind: "function",
        name: "open",
        signature: "function open(): void",
      },
    ]);
  });

  it("security#1 traceResolution in the tsconfig prints no raw PR text, so no line starts with ::", () => {
    const result = run(path.join(TESTDATA, "trace"), path.join(scratch, "trace.json"));
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /TS2307/);
    for (const line of `${result.stdout}\n${result.stderr}`.split("\n")) {
      assert.ok(!line.startsWith("::"), line);
      assert.ok(line === "" || line.startsWith("api snapshot (typescript): "), line);
    }
  });

  it("exits 2 on a type error and writes no entry file", () => {
    const out = path.join(scratch, "broken.json");
    const result = run(path.join(TESTDATA, "broken"), out);
    assert.equal(result.status, 2);
    assert.match(result.stderr, /TS2322/);
    assert.match(result.stderr, /src\/index\.ts/);
    assert.ok(!result.stderr.includes(TESTDATA), result.stderr);
    assert.throws(() => readFileSync(out));
  });

  for (const [name, setup, message] of [
    [
      "a missing tsconfig.json",
      (dir: string) => rmSync(path.join(dir, "tsconfig.json")),
      /no tsconfig\.json/,
    ],
    [
      "an unreadable package.json",
      (dir: string) => writeFileSync(path.join(dir, "package.json"), "{"),
      /package\.json/,
    ],
    [
      "an entry that is neither built nor on disk",
      (dir: string) => edit(path.join(dir, "package.json"), "./dist/extra.js", "./dist/missing.js"),
      /dist\/missing\.d\.ts/,
    ],
    [
      "two packages with the same name and subpath",
      (dir: string) =>
        writeFileSync(
          path.join(dir, "tools/package.json"),
          '{ "name": "basic", "types": "../src/index.ts" }',
        ),
      /unit "basic" comes from both "\." and "tools"/,
    ],
  ] as const) {
    it(`exits 2 on ${name}`, () => {
      const dir = copy("basic");
      setup(dir);
      const result = run(dir, path.join(scratch, "unused.json"));
      assert.equal(result.status, 2, result.stderr);
      assert.match(result.stderr, message);
      for (const line of result.stderr.split("\n")) assert.ok(!line.startsWith("::"), line);
    });
  }

  it("exits 2 when a declaration re-exports a module the reader cannot resolve", () => {
    const dir = copy("conditions");
    writeFileSync(path.join(dir, "lib/hand.d.ts"), 'export * from "./missing.js";\n');
    const result = run(dir, path.join(scratch, "unresolved.json"));
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /"lib\/hand\.d\.ts": cannot resolve "\.\/missing\.js"/);
  });

  it("exits 2 on an unresolvable import inside a reached ambient module, and checks only the reached one", () => {
    const result = run(
      path.join(TESTDATA, "ambientmissing"),
      path.join(scratch, "ambientmissing.json"),
    );
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /"src\/env\.d\.ts": cannot resolve "nope-missing"/);
    assert.ok(!result.stderr.includes("also-missing"), result.stderr);
  });

  it("exits 2 with usage on a wrong argument count", () => {
    const result = run(path.join(TESTDATA, "basic"), "");
    const usage = spawnSync(process.execPath, [SCRIPT], { encoding: "utf8" });
    assert.equal(usage.status, 2);
    assert.match(usage.stderr, /usage/);
    assert.equal(result.status, 2);
  });

  it("an empty project directory without packages writes []", () => {
    const dir = path.join(scratch, "empty");
    mkdirSync(dir);
    const out = path.join(scratch, "empty.json");
    assert.equal(run(dir, out).status, 0);
    assert.equal(readFileSync(out, "utf8"), "[]\n");
  });
});

describe("freeze scope", () => {
  const units = (entries: Entry[]): string[] => [...new Set(entries.map((e) => e.unit))];
  const whole = generate(path.join(TESTDATA, "scoped"));

  it("no scope or an empty one writes the whole project", () => {
    assert.deepEqual(units(whole), [
      "kvm",
      "kvm/driver",
      "kvm/driver/io",
      "kvm/driverx",
      "kvm/net/tcp",
      "plug",
      "plug/driver",
    ]);
    assert.deepEqual(generate(path.join(TESTDATA, "scoped"), []), whole);
  });

  it("a prefix keeps its subpath and those below it by segment, in every package", () => {
    const entries = generate(path.join(TESTDATA, "scoped"), ["./driver"]);
    assert.deepEqual(units(entries), ["kvm/driver", "kvm/driver/io", "plug/driver"]);
    assert.deepEqual(
      entries,
      whole.filter((e) => units(entries).includes(e.unit)),
    );
  });

  it("prefixes match expanded pattern subpaths, and . covers every subpath", () => {
    assert.deepEqual(units(generate(path.join(TESTDATA, "scoped"), ["./net", "./driver/io"])), [
      "kvm/driver/io",
      "kvm/net/tcp",
    ]);
    assert.deepEqual(generate(path.join(TESTDATA, "scoped"), ["."]), whole);
  });

  it("a new public export outside the scope leaves the entries unchanged", () => {
    const dir = copy("scoped");
    const before = generate(dir, ["./driver"]);
    writeFileSync(
      path.join(dir, "src/driverx.ts"),
      "export const x = 2;\nexport const added = 9;\n",
    );
    writeFileSync(
      path.join(dir, "plug/src/index.ts"),
      "export const plugged = 4;\nexport const added = 9;\n",
    );
    assert.deepEqual(generate(dir, ["./driver"]), before);
    assert.ok(generate(dir).some((e) => e.name === "added"));
  });

  it("a new public export inside the scope is a new entry", () => {
    const dir = copy("scoped");
    const before = generate(dir, ["./driver"]);
    writeFileSync(
      path.join(dir, "src/driver/io.ts"),
      "export const io = 1;\nexport const added = 9;\n",
    );
    const after = generate(dir, ["./driver"]);
    assert.deepEqual(
      after.filter((e) => !before.some((b) => keyOf(b) === keyOf(e))),
      [
        {
          unit: "kvm/driver/io",
          parent: "",
          kind: "const",
          name: "added",
          signature: "const added = 9",
        },
      ],
    );
  });

  it("a scope that matches no subpath writes no entries", () => {
    assert.deepEqual(generate(path.join(TESTDATA, "scoped"), ["./nope"]), []);
  });

  it("units outside the scope are not read", () => {
    const dir = copy("conditions");
    writeFileSync(path.join(dir, "lib/hand.d.ts"), 'export * from "./missing.js";\n');
    assert.throws(() => generate(dir), /cannot resolve "\.\/missing\.js"/);
    assert.deepEqual(
      generate(dir, ["./features"]).map((e) => e.unit),
      ["./features/a", "./features/b"],
    );
  });
});

// A repo root holding the scoped fixture at `pkg` and, when given, .github/checks/repo-guards.toml.
function repo(toml?: string): string {
  const root = mkdtempSync(path.join(scratch, "repo-"));
  cpSync(path.join(TESTDATA, "scoped"), path.join(root, "pkg"), { recursive: true });
  if (toml !== undefined) {
    mkdirSync(path.join(root, ".github/checks"), { recursive: true });
    writeFileSync(path.join(root, ".github/checks/repo-guards.toml"), toml);
  }
  return root;
}

describe("freeze scope from repo-guards.toml", () => {
  it("no file, no section, no key or only other languages' scopes mean the whole project", () => {
    for (const toml of [
      undefined,
      "",
      '[project."other"]\napi-snapshot-scope.typescript = ["./driver"]\n',
      '[project."pkg"]\napi-snapshot.typescript = ".github/checks/api-ts.json"\n',
      '[project."pkg"]\napi-snapshot-scope.rust = ["kvm_op::driver"]\n',
    ]) {
      assert.deepEqual(readScope(repo(toml), "pkg"), [], String(toml));
    }
  });

  it("reads api-snapshot-scope.typescript of the working directory's section, in order", () => {
    const root = repo(
      '[project."pkg"]\napi-snapshot-scope.typescript = ["./net", ".", "./driver/io"]\n',
    );
    assert.deepEqual(readScope(root, "pkg"), ["./net", ".", "./driver/io"]);
  });

  for (const [toml, message] of [
    ["[project", /^\.github\/checks\/repo-guards\.toml: /],
    ["project = 1\n", /^\[project\] is not a table$/],
    ['project."pkg" = 1\n', /^\[project\."pkg"\] is not a table$/],
    ['[project."pkg"]\napi-snapshot-scope = ["./a"]\n', /api-snapshot-scope is not a table$/],
    ['[project."pkg"]\napi-snapshot-scope.typescript = "./a"\n', /not an array of strings$/],
    ['[project."pkg"]\napi-snapshot-scope.typescript = [1]\n', /not an array of strings$/],
    ['[project."pkg"]\napi-snapshot-scope.typescript = []\n', /= \[\]: must list at least one/],
    ...["driver", "./", "./a//b", "./a/../b", "./a/./b", "./a/*", "*", "/a"].map(
      (prefix) =>
        [
          `[project."pkg"]\napi-snapshot-scope.typescript = [${JSON.stringify(prefix)}]\n`,
          `api-snapshot-scope.typescript: ${JSON.stringify(prefix)}: must be "." or an export subpath`,
        ] as const,
    ),
    [
      '[project."pkg"]\napi-snapshot-scope.typescript = ["./a\\u0001"]\n',
      /must not contain control characters$/,
    ],
  ] as const) {
    it(`exits 2 on ${JSON.stringify(toml)}`, () => {
      assert.throws(
        () => readScope(repo(toml), "pkg"),
        (error) =>
          error instanceof Failure &&
          (typeof message === "string"
            ? error.message.includes(message)
            : message.test(error.message)),
      );
    });
  }

  it("the command line reads the scope with --root and --working-directory", () => {
    const root = repo('[project."pkg"]\napi-snapshot-scope.typescript = ["./driver"]\n');
    const flags = ["--root", root, "--working-directory", "pkg"];
    const out = path.join(root, "entries.json");
    const result = run(path.join(root, "pkg"), out, flags);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(
      JSON.parse(readFileSync(out, "utf8")),
      generate(path.join(TESTDATA, "scoped"), ["./driver"]),
    );
    const whole = path.join(root, "whole.json");
    assert.equal(run(path.join(root, "pkg"), whole).status, 0);
    assert.deepEqual(
      JSON.parse(readFileSync(whole, "utf8")),
      generate(path.join(TESTDATA, "scoped")),
    );
  });

  it("takes a working directory that starts with - in the --flag=value form", () => {
    const root = repo('[project."-pkg"]\napi-snapshot-scope.typescript = ["./driver"]\n');
    cpSync(path.join(root, "pkg"), path.join(root, "-pkg"), { recursive: true });
    const out = path.join(root, "entries.json");
    const flags = [`--root=${root}`, "--working-directory=-pkg"];
    const result = run(path.join(root, "-pkg"), out, flags);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(
      JSON.parse(readFileSync(out, "utf8")),
      generate(path.join(TESTDATA, "scoped"), ["./driver"]),
    );
  });

  it("exits 2 on an invalid scope and writes no entry file", () => {
    const root = repo('[project."pkg"]\napi-snapshot-scope.typescript = ["./a/*"]\n');
    const out = path.join(root, "entries.json");
    const result = run(path.join(root, "pkg"), out, ["--root", root, "--working-directory", "pkg"]);
    assert.equal(result.status, 2, result.stderr);
    assert.match(
      result.stderr,
      /^api snapshot \(typescript\): \[project\."pkg"\] api-snapshot-scope\.typescript: "\.\/a\/\*"/,
    );
    assert.throws(() => readFileSync(out));
  });

  for (const flags of [
    ["--root", "."],
    ["--working-directory", "."],
    ["--scope", "./a"],
  ]) {
    it(`exits 2 with usage on ${flags.join(" ")}`, () => {
      const result = run(path.join(TESTDATA, "scoped"), path.join(scratch, "unused.json"), flags);
      assert.equal(result.status, 2);
      assert.match(result.stderr, /usage/);
    });
  }
});
