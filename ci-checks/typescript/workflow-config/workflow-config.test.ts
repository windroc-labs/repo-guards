import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, it, type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { run } from "./workflow-config.ts";

const SCRIPT = fileURLToPath(new URL("./workflow-config.ts", import.meta.url));
const SHARED = path.dirname(path.dirname(SCRIPT));

function tempDir(t: TestContext): string {
  const dir = mkdtempSync(path.join(tmpdir(), "workflow-config-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function write(dir: string, name: string, text: string): string {
  const file = path.join(dir, name);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, text);
  return file;
}

function lines(args: string[]): string[] {
  const out: string[] = [];
  run(args, (line) => out.push(line));
  return out;
}

describe("tsc-flags", () => {
  it("prints each compiler option as a flag line and a value line", (t) => {
    const file = write(tempDir(t), "tsconfig.json", '{"compilerOptions": {"strict": true, "target": "esnext"}}');
    assert.deepEqual(lines(["tsc-flags", file]), ["--strict\ntrue", "--target\nesnext"]);
  });

  it("reads the shared tsconfig.json", () => {
    assert.deepEqual(lines(["tsc-flags", path.join(SHARED, "tsconfig.json")]), [
      "--strict\ntrue",
      "--noUncheckedIndexedAccess\ntrue",
    ]);
  });
});

describe("coverage-include", () => {
  it("prints the knip.json project globs marked with a trailing ! without the mark", (t) => {
    const file = write(tempDir(t), "knip.json", '{"entry": ["x!"], "project": ["src/**/*.ts!", "scripts/*.ts", "lib/**!"]}');
    assert.deepEqual(lines(["coverage-include", file]), ["src/**/*.ts", "lib/**"]);
  });

  it("prints nothing when no glob is marked", (t) => {
    const file = write(tempDir(t), "knip.json", '{"project": ["src/**/*.ts"]}');
    assert.deepEqual(lines(["coverage-include", file]), []);
  });

  it("reads the shared knip.json", () => {
    assert.deepEqual(lines(["coverage-include", path.join(SHARED, "knip.json")]), ["src/**/*.ts"]);
  });
});

describe("coverage-exclude", () => {
  it("prints each exclude glob", (t) => {
    const file = write(tempDir(t), "coverage.json", '{"exclude": ["**/*.test.ts", "**/generated/**"]}');
    assert.deepEqual(lines(["coverage-exclude", file]), ["**/*.test.ts", "**/generated/**"]);
  });

  it("reads the shared coverage.json", () => {
    assert.ok(lines(["coverage-exclude", path.join(SHARED, "coverage.json")]).includes("**/*.d.ts"));
  });
});

describe("replace", () => {
  it("replaces every occurrence in the file", (t) => {
    const file = write(tempDir(t), "blob.json", '{"a": "/work/x", "b": ["/work/y", "/work/work/z"]}');
    assert.deepEqual(lines(["replace", file, "/work", "/root"]), []);
    assert.equal(readFileSync(file, "utf8"), '{"a": "/root/x", "b": ["/root/y", "/root/root/z"]}');
  });

  it("leaves a file without the text unchanged", (t) => {
    const file = write(tempDir(t), "blob.json", "abc");
    run(["replace", file, "zz", "y"], () => {});
    assert.equal(readFileSync(file, "utf8"), "abc");
  });
});

describe("command line", () => {
  const cli = (...args: string[]) => spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });

  it("prints results on stdout and exits 0", (t) => {
    const result = cli("coverage-include", write(tempDir(t), "knip.json", '{"project": ["a!", "b!"]}'));
    assert.deepEqual([result.status, result.stdout, result.stderr], [0, "a\nb\n", ""]);
  });

  it("rejects an unknown command or a wrong argument count with exit code 2", () => {
    for (const args of [[], ["nope"], ["tsc-flags"], ["replace", "f", "a"], ["license-allow", "d", "f"], ["knip-config", "a", "b", "c", "d"]]) {
      const result = cli(...args);
      assert.equal(result.status, 2, args.join(" "));
      assert.match(result.stderr, /usage:/);
    }
  });

  it("reports a failure such as a missing file with exit code 2", (t) => {
    const result = cli("tsc-flags", path.join(tempDir(t), "absent.json"));
    assert.equal(result.status, 2);
    assert.match(result.stderr, /^workflow-config: /);
  });
});
