import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseToml, TomlError } from "smol-toml";
import ts from "typescript";

export const CONFIG = ".github/checks/typescript/comment-budget.toml";
const KEYS = ["source_percent", "test_percent", "allowance_chars", "test_paths"] as const;
const INTEGER_KEYS = ["source_percent", "test_percent", "allowance_chars"] as const;
export const PURPOSE =
  "The comment budget keeps comments short: meet it by cutting comments or doc comments, " +
  "never by adding code to lower the ratio.";
const USAGE = "usage: comment-budget.ts [--base REF] [--config PATH] [--all]";
const EXTENSIONS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"];
const DECLARATION = /\.d\.[mc]?ts$/;

export class BudgetError extends Error {}

export interface Counts {
  comment: number;
  code: number;
}

export interface Measure {
  source: Counts;
  tests: Counts;
}

const zero = (): Counts => ({ comment: 0, code: 0 });

const add = (a: Counts, b: Counts): Counts => ({
  comment: a.comment + b.comment,
  code: a.code + b.code,
});

export function chars(text: string): number {
  return [...text.replace(/\s+/gu, "")].length;
}

function syntaxError(text: string, fileName: string): string | undefined {
  const { diagnostics = [] } = ts.transpileModule(text, {
    fileName,
    reportDiagnostics: true,
    compilerOptions: {},
  });
  const first = diagnostics[0];
  if (first === undefined) return undefined;
  const line = first.file?.getLineAndCharacterOfPosition(first.start ?? 0).line;
  const message = ts.flattenDiagnosticMessageText(first.messageText, "\n");
  return line === undefined ? message : `line ${line + 1}: ${message}`;
}

function commentChars(text: string, fileName: string): number {
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, false);
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false);
  let total = 0;
  const scan = (start: number, end: number): void => {
    scanner.setText(text, start, end - start);
    for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFileToken; kind = scanner.scan()) {
      if (
        kind === ts.SyntaxKind.SingleLineCommentTrivia ||
        kind === ts.SyntaxKind.MultiLineCommentTrivia
      ) {
        total += chars(text.slice(scanner.getTokenStart(), scanner.getTokenEnd()));
      }
    }
  };
  const visit = (node: ts.Node): void => {
    const children: ts.Node[] = [];
    ts.forEachChild(node, (child) => {
      children.push(child);
    });
    if (children.length === 0) {
      scan(node.pos, node.kind <= ts.SyntaxKind.LastToken ? node.getStart(file) : node.end);
      return;
    }
    children.sort((a, b) => a.pos - b.pos);
    let pos = node.pos;
    for (const child of children) {
      scan(pos, child.pos);
      visit(child);
      pos = child.end;
    }
    scan(pos, node.end);
  };
  visit(file);
  return total;
}

export function measure(text: string, fileName: string, wholeFileIsTest: boolean): Measure {
  const error = syntaxError(text, fileName);
  if (error !== undefined) throw new BudgetError(error);
  const comment = commentChars(text, fileName);
  const counts = { comment, code: chars(text) - comment };
  return wholeFileIsTest ? { source: zero(), tests: counts } : { source: counts, tests: zero() };
}

export function glob(pattern: string, file: string): boolean {
  const segment = (p: string, s: string): boolean => {
    const star = p.indexOf("*");
    if (star < 0) return p === s;
    const head = p.slice(0, star);
    if (!s.startsWith(head)) return false;
    const rest = [...s.slice(head.length)];
    const tail = p.slice(star + 1);
    return rest.some((_, k) => segment(tail, rest.slice(k).join(""))) || segment(tail, "");
  };
  const segments = (p: string[], s: string[]): boolean => {
    const [first, ...rest] = p;
    if (first === undefined) return s.length === 0;
    if (first === "**") return s.some((_, k) => segments(rest, s.slice(k))) || segments(rest, []);
    const [name, ...tail] = s;
    return name !== undefined && segment(first, name) && segments(rest, tail);
  };
  return segments(pattern.split("/"), file.split("/"));
}

export interface Config {
  source_percent: number;
  test_percent: number;
  allowance_chars: number;
  test_paths: string[];
}

export function isTestPath(config: Config, file: string): boolean {
  return config.test_paths.some((p) => glob(p, file));
}

export function parseConfig(text: string): Config {
  let values: Record<string, unknown>;
  try {
    values = parseToml(text, { integersAsBigInt: true });
  } catch (error) {
    if (!(error instanceof TomlError)) throw error;
    const reason = error.message.split("\n")[0] ?? "";
    throw new BudgetError(`line ${error.line}: ${reason}`);
  }
  for (const key of Object.keys(values)) {
    if (!(KEYS as readonly string[]).includes(key)) throw new BudgetError(`unknown key \`${key}\``);
  }
  for (const key of KEYS) {
    if (!(key in values)) throw new BudgetError(`missing key \`${key}\``);
  }
  const integers = INTEGER_KEYS.map((key) => {
    const n = values[key];
    if (typeof n !== "bigint" || n < 0n || n > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new BudgetError(`\`${key}\` must be an integer`);
    }
    return Number(n);
  });
  const [source_percent = 0, test_percent = 0, allowance_chars = 0] = integers;
  const paths = values["test_paths"];
  if (!Array.isArray(paths) || !paths.every((p) => typeof p === "string")) {
    throw new BudgetError("`test_paths` must be a list of strings");
  }
  return { source_percent, test_percent, allowance_chars, test_paths: paths };
}

export interface Options {
  base: string;
  config: string | undefined;
  all: boolean;
}

export function parseArgs(args: readonly string[]): Options {
  const options: Options = { base: "origin/main", config: undefined, all: false };
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (flag === "--all") {
      options.all = true;
      continue;
    }
    if (flag !== "--base" && flag !== "--config") {
      throw new BudgetError(`unknown argument ${flag}\n${USAGE}`);
    }
    const value = args[++i];
    if (value === undefined) throw new BudgetError(`${flag} needs a value`);
    if (flag === "--base") options.base = value;
    else options.config = value;
  }
  return options;
}

function git(root: string, ...args: string[]): string {
  const done = spawnSync(
    "git",
    ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", root, ...args],
    { encoding: "utf8", maxBuffer: 1 << 30 },
  );
  if (done.error) throw new BudgetError(`git: ${done.error.message}`);
  if (done.status !== 0) throw new BudgetError(`git ${args[0]} failed: ${done.stderr.trim()}`);
  return done.stdout;
}

function isChecked(root: string, file: string): boolean {
  if (!EXTENSIONS.some((e) => file.endsWith(e)) || DECLARATION.test(file)) return false;
  const full = path.join(root, file);
  return existsSync(full) && statSync(full).isFile();
}

function files(root: string, options: Options): string[] {
  let listed: string;
  if (options.all) {
    listed = git(root, "ls-files", "-z", "--", ...EXTENSIONS.map((e) => `*${e}`));
  } else {
    const fork = git(root, "merge-base", options.base, "HEAD").trim();
    listed = git(root, "diff", "--name-only", "--no-renames", "--diff-filter=d", "-z", fork, "--");
  }
  return listed
    .split("\0")
    .filter((p) => isChecked(root, p))
    .sort();
}

function percent(n: number, code: number): string {
  if (code === 0) return n > 0 ? "inf" : "0.00%";
  return `${((n * 100) / code).toFixed(2)}%`;
}

function readUtf8(file: string): string {
  return new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(file));
}

const message = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

export function run(args: readonly string[], cwd: string, out: (line: string) => void): boolean {
  const options = parseArgs(args);
  const root = git(cwd, "rev-parse", "--show-toplevel").trim();
  const configPath = options.config ? path.resolve(cwd, options.config) : path.join(root, CONFIG);
  let config: Config;
  try {
    config = parseConfig(readUtf8(configPath));
  } catch (error) {
    throw new BudgetError(`config ${configPath}: ${message(error)}`);
  }
  const measured: [string, Measure][] = [];
  for (const file of files(root, options)) {
    try {
      const text = readUtf8(path.join(root, file));
      measured.push([file, measure(text, file, isTestPath(config, file))]);
    } catch (error) {
      throw new BudgetError(`${file}: ${message(error)}`);
    }
  }
  const allowance = config.allowance_chars;
  const limit = (m: Measure): number =>
    config.source_percent * m.source.code + config.test_percent * m.tests.code;
  const over = measured.filter(
    ([, m]) => (m.source.comment + m.tests.comment) * 100 > limit(m) + allowance * 100,
  );
  const scope = options.all ? "file" : "changed file";
  const name =
    `comment budget (source ${config.source_percent}%, tests ${config.test_percent}% ` +
    `of code + ${allowance} characters per ${scope})`;
  if (over.length === 0) {
    out(`ok ${name}: ${measured.length} checked`);
  } else {
    out(`FAIL ${name}: ${over.length} of ${measured.length} over budget`);
    for (const [file, m] of over) {
      const n = m.source.comment + m.tests.comment;
      const code = m.source.code + m.tests.code;
      const allowed = Math.floor(limit(m) / 100) + allowance;
      out(
        `  ${file}: ${percent(n, code)} (${n} comment / ${code} code characters, ` +
          `${m.tests.code} in tests; ${allowed} allowed, ${n - allowed} over)`,
      );
    }
  }
  const total = measured.reduce(
    (t, [, m]) => ({ source: add(t.source, m.source), tests: add(t.tests, m.tests) }),
    { source: zero(), tests: zero() },
  );
  for (const [label, c] of [
    ["source", total.source],
    ["tests", total.tests],
  ] as const) {
    out(
      `total ${label}: ${c.comment} comment / ${c.code} code characters ` +
        `(${percent(c.comment, c.code)})`,
    );
  }
  if (over.length > 0) out(PURPOSE);
  return over.length === 0;
}

function main(): number {
  try {
    return run(process.argv.slice(2), process.cwd(), (line) => console.log(line)) ? 0 : 1;
  } catch (error) {
    console.error(`comment-budget: ${message(error)}`);
    return 2;
  }
}

const entry = process.argv[1];
if (entry !== undefined && realpathSync(entry) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
