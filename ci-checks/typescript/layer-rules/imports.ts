/**
 * Layer rules scanner for TypeScript; protocol in ci-checks/python/layer-rules/imports.py.
 * Reference sites: import (type), export … from, import = require, require("x"), import("x") types; not dynamic import().
 * Specifiers resolve as the file's tsconfig says, through a host that sees only the request's files and reads only
 * its context files: no node_modules, no file outside the side root, nothing of the caller's is run.
 */
import { readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type TS from "typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function fail(message: string): never {
  process.stderr.write(`imports.ts: ${message.split("\n")[0]}\n`);
  process.exit(2);
}

let ts: typeof TS;
try {
  ts = (await import("typescript")).default;
} catch {
  fail(`typescript is not installed: run npm ci --ignore-scripts in ${HERE}`);
}

export interface Request {
  files: string[];
  scan: string[];
  context: string[];
  settings: Record<string, unknown>;
}

export type Output = { file: string; line: number; targets: string[] } | { file: string; error: string };

const KINDS: Record<string, TS.ScriptKind> = {
  ".ts": ts.ScriptKind.TS,
  ".mts": ts.ScriptKind.TS,
  ".cts": ts.ScriptKind.TS,
  ".tsx": ts.ScriptKind.TSX,
  ".js": ts.ScriptKind.JS,
  ".mjs": ts.ScriptKind.JS,
  ".cjs": ts.ScriptKind.JS,
  ".jsx": ts.ScriptKind.JSX,
};

type Host = TS.ModuleResolutionHost & TS.ParseConfigHost;
type MatchFiles = (
  root: string,
  extensions: readonly string[] | undefined,
  excludes: readonly string[] | undefined,
  includes: readonly string[] | undefined,
  useCaseSensitiveFileNames: boolean,
  currentDirectory: string,
  depth: number | undefined,
  getEntries: (dir: string) => { files: string[]; directories: string[] },
  realpath: (p: string) => string,
) => string[];

/** A module resolution and config host over the request's paths, confined to the side root. */
export function makeHost(root: string, request: Request): Host {
  const base = path.resolve(root);
  const known = new Set([...request.files, ...request.context, ...request.scan]);
  const readable = new Set(request.context);
  const entries = new Map<string, { files: Set<string>; directories: Set<string> }>();
  const entry = (dir: string) => {
    let found = entries.get(dir);
    if (found === undefined) entries.set(dir, (found = { files: new Set(), directories: new Set() }));
    return found;
  };
  entry("");
  for (const file of known) {
    const parts = file.split("/");
    entry(parts.slice(0, -1).join("/")).files.add(parts.at(-1) ?? "");
    for (let i = 1; i < parts.length; i++) entry(parts.slice(0, i - 1).join("/")).directories.add(parts[i - 1] ?? "");
  }
  // The path relative to the side root, or undefined outside it.
  const inside = (p: string): string | undefined => {
    const relative = path.relative(base, path.resolve(base, p));
    return relative.split(path.sep)[0] === ".." || path.isAbsolute(relative) ? undefined : relative.split(path.sep).join("/");
  };
  const getEntries = (dir: string) => {
    const found = entries.get(inside(dir) ?? "\0");
    return { files: [...(found?.files ?? [])], directories: [...(found?.directories ?? [])] };
  };
  const matchFiles = (ts as unknown as { matchFiles: MatchFiles }).matchFiles;
  return {
    useCaseSensitiveFileNames: true,
    getCurrentDirectory: () => base,
    realpath: (p) => p,
    fileExists: (p) => known.has(inside(p) ?? "\0"),
    directoryExists: (p) => entries.has(inside(p) ?? "\0"),
    getDirectories: (p) => getEntries(p).directories,
    readFile: (p) => {
      const relative = inside(p);
      if (relative === undefined || !readable.has(relative)) return undefined;
      try {
        return readFileSync(path.join(base, relative), "utf8");
      } catch {
        return undefined;
      }
    },
    readDirectory: (dir, extensions, excludes, includes, depth) =>
      matchFiles(dir, extensions, excludes, includes, true, base, depth, getEntries, (p) => p),
  };
}

interface Project {
  options: TS.CompilerOptions;
  cache: TS.ModuleResolutionCache;
}

const DEFAULTS: TS.CompilerOptions = { module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler };

/** Picks each file's tsconfig like an editor does: the nearest tsconfig.json, or the project it references that holds the file. */
class Projects {
  private parsed = new Map<string, TS.ParsedCommandLine | undefined>();
  private projects = new Map<string, Project>();

  private base: string;
  private host: Host;

  constructor(base: string, host: Host) {
    this.base = base;
    this.host = host;
  }

  private parse(config: string): TS.ParsedCommandLine | undefined {
    if (!this.parsed.has(config)) {
      const { config: json } = ts.readConfigFile(config, (p) => this.host.readFile(p));
      const result = json === undefined ? undefined : ts.parseJsonConfigFileContent(json, this.host, path.dirname(config), undefined, config);
      this.parsed.set(config, result);
    }
    return this.parsed.get(config);
  }

  private nearest(file: string): string | undefined {
    for (let dir = path.dirname(file); ; dir = path.dirname(dir)) {
      const config = path.join(dir, "tsconfig.json");
      if (this.host.fileExists(config)) return config;
      if (dir === this.base || path.dirname(dir) === dir) return undefined;
    }
  }

  private owner(file: string): string | undefined {
    const nearest = this.nearest(file);
    if (nearest === undefined) return undefined;
    const queue = [nearest];
    const seen = new Set<string>();
    for (let config = queue.shift(); config !== undefined; config = queue.shift()) {
      if (seen.has(config)) continue;
      seen.add(config);
      const parsed = this.parse(config);
      if (parsed === undefined) continue;
      if (parsed.fileNames.includes(file)) return config;
      for (const reference of parsed.projectReferences ?? []) queue.push(ts.resolveProjectReferencePath(reference));
    }
    return nearest;
  }

  for(file: string): Project {
    const config = this.owner(file) ?? "";
    let project = this.projects.get(config);
    if (project === undefined) {
      const options = { ...(config ? (this.parse(config)?.options ?? {}) : DEFAULTS), allowJs: true, noEmit: true };
      project = { options, cache: ts.createModuleResolutionCache(this.base, (p) => p, options) };
      this.projects.set(config, project);
    }
    return project;
  }
}

function specifiers(node: TS.Node): TS.StringLiteralLike | undefined {
  if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier !== undefined) {
    return ts.isStringLiteral(node.moduleSpecifier) ? node.moduleSpecifier : undefined;
  }
  if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
    const expression = node.moduleReference.expression;
    return ts.isStringLiteral(expression) ? expression : undefined;
  }
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "require") {
    const [argument, ...rest] = node.arguments;
    return argument !== undefined && rest.length === 0 && ts.isStringLiteralLike(argument) ? argument : undefined;
  }
  if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal)) {
    return node.argument.literal;
  }
  return undefined;
}

type Diagnostics = { parseDiagnostics?: readonly TS.Diagnostic[] };

function scanFile(file: string, bytes: Uint8Array, base: string, host: Host, projects: Projects, files: Set<string>): Output[] {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return [{ file, error: "not valid UTF-8" }];
  }
  const absolute = path.join(base, file);
  const { options, cache } = projects.for(absolute);
  const impliedNodeFormat = ts.getImpliedNodeFormatForFile(absolute, cache.getPackageJsonInfoCache(), host, options);
  const source = ts.createSourceFile(
    absolute,
    text,
    { languageVersion: ts.ScriptTarget.Latest, impliedNodeFormat, jsDocParsingMode: ts.JSDocParsingMode.ParseNone },
    true,
    KINDS[path.extname(file).toLowerCase()] ?? ts.ScriptKind.TS,
  );
  const first = (source as TS.SourceFile & Diagnostics).parseDiagnostics?.[0];
  if (first !== undefined) {
    const line = source.getLineAndCharacterOfPosition(first.start ?? 0).line + 1;
    return [{ file, error: `line ${line}: ${ts.flattenDiagnosticMessageText(first.messageText, "\n")}` }];
  }
  const sites: [TS.Node, TS.StringLiteralLike][] = [];
  const visit = (node: TS.Node): void => {
    const specifier = specifiers(node);
    if (specifier !== undefined) sites.push([node, specifier]);
    ts.forEachChild(node, visit);
  };
  visit(source);
  const records: Output[] = [];
  for (const [node, specifier] of sites.sort(([a], [b]) => a.getStart(source) - b.getStart(source))) {
    const mode = ts.getModeForUsageLocation(source, specifier, options);
    const resolved = ts.resolveModuleName(specifier.text, absolute, options, host, cache, undefined, mode).resolvedModule;
    if (resolved === undefined || resolved.isExternalLibraryImport) continue;
    const target = path.relative(base, resolved.resolvedFileName).split(path.sep).join("/");
    if (target === file || !files.has(target)) continue;
    const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    records.push({ file, line, targets: [target] });
  }
  return records;
}

export function scanRequest(root: string, request: Request): Output[] {
  const base = path.resolve(root);
  const host = makeHost(base, request);
  const projects = new Projects(base, host);
  const files = new Set(request.files);
  return request.scan.flatMap((file) => scanFile(file, readFileSync(path.join(base, file)), base, host, projects, files));
}

function paths(value: unknown, key: string): string[] {
  if (!Array.isArray(value) || !value.every((p) => typeof p === "string")) throw new Error(`${key} is not a list of strings`);
  for (const p of value as string[]) {
    if (p === "" || path.posix.isAbsolute(p) || p.split("/").includes("..")) throw new Error(`bad path ${JSON.stringify(p)}`);
  }
  return value as string[];
}

function parseRequest(text: string): Request {
  const raw: unknown = JSON.parse(text);
  if (typeof raw !== "object" || raw === null) throw new Error("the request is not a JSON object");
  const object = raw as Record<string, unknown>;
  const settings = object["settings"];
  return {
    files: paths(object["files"], "files"),
    scan: paths(object["scan"], "scan"),
    context: paths(object["context"], "context"),
    settings: typeof settings === "object" && settings !== null ? (settings as Record<string, unknown>) : {},
  };
}

function main(argv: string[]): void {
  const [root, ...rest] = argv;
  if (root === undefined || rest.length > 0) fail("usage: imports.ts <side root>  (JSON request on stdin)");
  let lines: string[];
  try {
    lines = scanRequest(root, parseRequest(readFileSync(0, "utf8"))).map((record) => JSON.stringify(record));
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
  if (lines.length > 0) process.stdout.write(`${lines.join("\n")}\n`);
}

const script = process.argv[1];
if (script !== undefined && realpathSync(script) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
