import {
  existsSync,
  readdirSync,
  readFileSync,
  realpathSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export interface Entry {
  unit: string;
  parent: string;
  kind: string;
  name: string;
  signature: string;
}

export class Failure extends Error {}

interface Unit {
  name: string;
  declaration: string;
}

interface Package {
  dir: string;
  config: string;
  // null: Node resolves the subpath to nothing (a null target, or no condition this generator accepts).
  targets: Map<string, string | null>;
  name: string;
}

// Everything the build emits, kept in memory: the build never writes a file, whatever the tsconfig says.
interface Outputs {
  text: Map<string, string>;
  dirs: Set<string>;
  source: Map<string, ts.SourceFile>;
  declarationOf: Map<string, string>;
  jsOf: Map<string, string>;
  options: Map<string, ts.CompilerOptions>;
  // Per build: its own declaration files (hand-written and emitted), which may declare ambient modules.
  declarations: Map<ts.CompilerOptions, string[]>;
  // tsc's resolution of every import the build saw, per containing source file and resolutionKey.
  resolutions: Map<string, Map<string, ts.ResolvedModuleWithFailedLookupLocations>>;
}

type Resolutions = Map<string, Map<string, ts.ResolvedModuleWithFailedLookupLocations>>;

function resolutionKey(literal: ts.StringLiteralLike, mode: ts.ResolutionMode): string {
  return `${mode ?? ""}:${literal.text}`;
}

// Resolves with tsc's own module resolution, as the host's default would, and records each result.
function recordingResolver(
  host: ts.CompilerHost,
  options: ts.CompilerOptions,
  record: Resolutions,
  first?: (
    literal: ts.StringLiteralLike,
    mode: ts.ResolutionMode,
    containing: ts.SourceFile,
  ) => ts.ResolvedModuleWithFailedLookupLocations | undefined,
  finish: (
    result: ts.ResolvedModuleWithFailedLookupLocations,
  ) => ts.ResolvedModuleWithFailedLookupLocations = (result) => result,
): NonNullable<ts.CompilerHost["resolveModuleNameLiterals"]> {
  const cache = ts.createModuleResolutionCache(
    host.getCurrentDirectory(),
    (file) => host.getCanonicalFileName(file),
    options,
  );
  return (literals, containingFile, redirected, compilerOptions, containing) => {
    const resolved = record.get(containingFile) ?? new Map();
    record.set(containingFile, resolved);
    return literals.map((literal) => {
      const mode = ts.getModeForUsageLocation(containing, literal, compilerOptions);
      const result = finish(
        first?.(literal, mode, containing) ??
          ts.resolveModuleName(
            literal.text,
            containingFile,
            compilerOptions,
            host,
            cache,
            redirected,
            mode,
          ),
      );
      resolved.set(resolutionKey(literal, mode), result);
      return result;
    });
  };
}

const PREFIX = "api snapshot (typescript): ";
const DECLARATION = /\.d\.[cm]?ts$/;
const JS = /\.[cm]?jsx?$/;
const TS = /\.[cm]?tsx?$/;
// Diagnostics about rewriting .ts import extensions, which only concern the emitted .js that nothing runs.
const REWRITE_ONLY = new Set([2876, 2877, 2878]);
const CONDITIONS = new Set(["types", "import", "require", "node", "default"]);
const DROPPED_MODIFIERS = new Set([
  ts.SyntaxKind.ExportKeyword,
  ts.SyntaxKind.DeclareKeyword,
  ts.SyntaxKind.DefaultKeyword,
]);
const printer = ts.createPrinter({ removeComments: true, newLine: ts.NewLineKind.LineFeed });

export function quote(text: string): string {
  // No raw line breaks, so no log line starts with "::"; "#" is escaped against the legacy "##[command]" syntax.
  return JSON.stringify(text).replaceAll("#", "\\u0023");
}

function fail(message: string): never {
  throw new Failure(message);
}

export function generate(projectDir: string): Entry[] {
  const root = path.resolve(projectDir);
  const rel = (file: string): string => path.relative(root, file).split(path.sep).join("/") || ".";
  const packages = findPackages(root, root, rel);
  const outputs: Outputs = {
    text: new Map(),
    dirs: new Set(),
    source: new Map(),
    declarationOf: new Map(),
    jsOf: new Map(),
    options: new Map(),
    declarations: new Map(),
    resolutions: new Map(),
  };
  const builds = new Map<string, ts.CompilerOptions>();
  for (const pkg of packages) build(pkg.config, outputs, builds, rel);
  const byOptions = new Map<ts.CompilerOptions, Unit[]>();
  const owner = new Map<string, string>();
  for (const pkg of packages) {
    for (const unit of units(pkg, outputs, rel)) {
      const other = owner.get(unit.name);
      if (other !== undefined) {
        fail(
          `unit ${quote(unit.name)} comes from both ${quote(other)} and ${quote(rel(pkg.dir))}: give each package.json its own name`,
        );
      }
      owner.set(unit.name, rel(pkg.dir));
      // Read each declaration with the options of the build that emitted it.
      const options = outputs.options.get(unit.declaration) ?? builds.get(pkg.config) ?? {};
      byOptions.set(options, [...(byOptions.get(options) ?? []), unit]);
    }
  }
  const entries = new Map<string, Entry>();
  for (const [options, optionUnits] of byOptions) read(optionUnits, options, outputs, entries, rel);
  return [...entries.values()].sort(compare);
}

function compare(a: Entry, b: Entry): number {
  for (const field of ["unit", "parent", "kind", "name"] as const) {
    if (a[field] !== b[field]) return a[field] < b[field] ? -1 : 1;
  }
  return 0;
}

function findPackages(dir: string, root: string, rel: (file: string) => string): Package[] {
  const found: Package[] = [];
  const entries = readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name < b.name ? -1 : a.name > b.name ? 1 : 0,
  );
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== "node_modules" && !entry.name.startsWith(".")) {
      found.push(...findPackages(full, root, rel));
    } else if (entry.isFile() && entry.name === "package.json") {
      const pkg = readPackage(full, root, rel);
      if (pkg !== undefined) found.push(pkg);
    }
  }
  return found;
}

function readPackage(
  file: string,
  root: string,
  rel: (file: string) => string,
): Package | undefined {
  let json: unknown;
  try {
    json = JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    fail(
      `cannot read ${quote(rel(file))}: ${quote(error instanceof Error ? error.message : String(error))}`,
    );
  }
  if (typeof json !== "object" || json === null || Array.isArray(json))
    fail(`${quote(rel(file))} is not a JSON object`);
  const manifest = json as Record<string, unknown>;
  const dir = path.dirname(file);
  const targets = entryTargets(manifest);
  if (targets.size === 0) return undefined;
  return {
    dir,
    config:
      nearestConfig(dir, root) ??
      fail(`${quote(rel(dir))}: no tsconfig.json in it or above it up to the working directory`),
    targets,
    name: typeof manifest["name"] === "string" ? manifest["name"] : rel(dir),
  };
}

function entryTargets(manifest: Record<string, unknown>): Map<string, string | null> {
  const exports = manifest["exports"];
  if (exports === undefined || exports === null) {
    const types = manifest["types"] ?? manifest["typings"];
    return new Map(typeof types === "string" ? [[".", types]] : []);
  }
  const isSubpathMap =
    typeof exports === "object" &&
    !Array.isArray(exports) &&
    Object.keys(exports).some((key) => key.startsWith("."));
  const map: Record<string, unknown> = isSubpathMap
    ? (exports as Record<string, unknown>)
    : { ".": exports };
  return new Map(Object.entries(map).map(([subpath, value]) => [subpath, pick(value) ?? null]));
}

// Node's rule: the first listed condition this generator accepts wins; null excludes the subpath.
function pick(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const target = pick(item);
      if (target !== undefined) return target;
    }
    return undefined;
  }
  if (typeof value === "object" && value !== null) {
    for (const [condition, target] of Object.entries(value)) {
      if (!CONDITIONS.has(condition)) continue;
      const picked = pick(target);
      if (picked !== undefined) return picked;
    }
  }
  return undefined;
}

function nearestConfig(dir: string, root: string): string | undefined {
  for (let current = dir; ; current = path.dirname(current)) {
    const config = path.join(current, "tsconfig.json");
    if (existsSync(config)) return config;
    if (current === root || path.dirname(current) === current) return undefined;
  }
}

function describeDiagnostics(
  diagnostics: readonly ts.Diagnostic[],
  rel: (file: string) => string,
): string {
  return diagnostics
    .map((d) => {
      const text = `TS${d.code} ${quote(ts.flattenDiagnosticMessageText(d.messageText, "\n"))}`;
      if (d.file === undefined || d.start === undefined) return text;
      const { line, character } = d.file.getLineAndCharacterOfPosition(d.start);
      return `${quote(rel(d.file.fileName))}:${line + 1}:${character + 1}: ${text}`;
    })
    .join("\n");
}

// Builds the config in memory, after the projects it references, each once.
function build(
  config: string,
  outputs: Outputs,
  builds: Map<string, ts.CompilerOptions>,
  rel: (file: string) => string,
): void {
  if (builds.has(config)) return;
  builds.set(config, {});
  const configErrors: ts.Diagnostic[] = [];
  const parsed = ts.getParsedCommandLineOfConfigFile(config, undefined, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (d) => configErrors.push(d),
  });
  if (parsed === undefined || configErrors.length > 0) {
    fail(`cannot read ${quote(rel(config))}:\n${describeDiagnostics(configErrors, rel)}`);
  }
  for (const reference of parsed.projectReferences ?? []) {
    build(ts.resolveProjectReferencePath(reference), outputs, builds, rel);
  }
  const { outFile: _outFile, ...projectOptions } = parsed.options;
  const rewriting =
    projectOptions.allowImportingTsExtensions === true &&
    projectOptions.rewriteRelativeImportExtensions !== true;
  const options: ts.CompilerOptions = {
    ...projectOptions,
    noEmit: false,
    declaration: true,
    emitDeclarationOnly: false,
    noEmitOnError: false,
    sourceMap: false,
    inlineSourceMap: false,
    declarationMap: false,
    // The compiler would print resolution traces, PR-chosen text, raw to stdout.
    traceResolution: false,
    // allowImportingTsExtensions is legal with emit only when the .ts import extensions are rewritten.
    ...(rewriting ? { rewriteRelativeImportExtensions: true } : {}),
  };
  builds.set(config, options);
  const host = ts.createCompilerHost(options, true);
  delete host.trace;
  host.resolveModuleNameLiterals = recordingResolver(host, options, outputs.resolutions);
  host.writeFile = (file) => {
    throw new Error(`the compiler tried to write ${quote(rel(file))}`);
  };
  // This program reads a referenced project's sources in place of its declaration output.
  Object.assign(host, { useSourceOfProjectReferenceRedirect: () => true });
  const program = ts.createProgram({
    rootNames: parsed.fileNames,
    options,
    host,
    ...(parsed.projectReferences === undefined
      ? {}
      : { projectReferences: parsed.projectReferences }),
    configFileParsingDiagnostics: ts.getConfigFileParsingDiagnostics(parsed),
  });
  const errors = (diagnostics: readonly ts.Diagnostic[]): ts.Diagnostic[] =>
    diagnostics.filter(
      (d) => d.category === ts.DiagnosticCategory.Error && !(rewriting && REWRITE_ONLY.has(d.code)),
    );
  let failed = errors(ts.getPreEmitDiagnostics(program));
  if (failed.length === 0) {
    const result = program.emit(undefined, (file, text, _bom, _onError, sources) => {
      outputs.text.set(file, text);
      outputs.options.set(file, options);
      for (let dir = path.dirname(file); !outputs.dirs.has(dir); dir = path.dirname(dir))
        outputs.dirs.add(dir);
      const source = sources?.length === 1 ? sources[0] : undefined;
      if (source === undefined) return;
      outputs.source.set(file, source);
      if (DECLARATION.test(file)) outputs.declarationOf.set(source.fileName, file);
      else if (JS.test(file)) outputs.jsOf.set(source.fileName, file);
    });
    failed = errors(result.diagnostics);
    const own = program
      .getSourceFiles()
      .filter(
        (file) =>
          file.isDeclarationFile &&
          !program.isSourceFileFromExternalLibrary(file) &&
          !program.isSourceFileDefaultLibrary(file),
      )
      .map((file) => file.fileName);
    const emitted = [...outputs.options]
      .filter(([file, built]) => built === options && DECLARATION.test(file))
      .map(([file]) => file);
    outputs.declarations.set(options, [...own, ...emitted]);
  }
  if (failed.length > 0)
    fail(`the build with ${quote(rel(config))} failed:\n${describeDiagnostics(failed, rel)}`);
}

function toDeclaration(target: string, outputs: Outputs): string | undefined {
  if (DECLARATION.test(target)) return target;
  if (JS.test(target)) return target.replace(/\.([cm]?)jsx?$/, ".d.$1ts");
  if (TS.test(target)) return outputs.declarationOf.get(target);
  return undefined;
}

// Node's PATTERN_KEY_COMPARE: an exact key wins, then the pattern with the longest part before "*".
function winner(subpath: string, keys: string[]): string | undefined {
  if (keys.includes(subpath)) return subpath;
  return keys
    .filter((key) => {
      const [head = "", tail = ""] = key.split("*", 2);
      return (
        key.includes("*") &&
        subpath.length > head.length + tail.length &&
        subpath.startsWith(head) &&
        subpath.endsWith(tail)
      );
    })
    .sort((a, b) => b.indexOf("*") - a.indexOf("*") || b.length - a.length)[0];
}

function isFile(file: string): boolean {
  try {
    return statSync(file).isFile();
  } catch {
    return false;
  }
}

function units(pkg: Package, outputs: Outputs, rel: (file: string) => string): Unit[] {
  const found: Unit[] = [];
  const keys = [...pkg.targets.keys()];
  for (const [subpath, target] of pkg.targets) {
    if (target === null) continue;
    const absolute = path.resolve(pkg.dir, target);
    const unitName = (sub: string): string => pkg.name + sub.slice(1);
    if (subpath.includes("*")) {
      const [head = "", tail = ""] = absolute.split("*", 2);
      // The build's modules: emitted .js and compiled sources, and emitted .d.ts only when the target names one.
      const pool = new Set([
        ...outputs.jsOf.values(),
        ...outputs.declarationOf.keys(),
        ...(DECLARATION.test(tail) ? outputs.declarationOf.values() : []),
      ]);
      const matches = [...pool]
        .filter(
          (file) =>
            file.length > head.length + tail.length && file.startsWith(head) && file.endsWith(tail),
        )
        .sort();
      if (matches.length === 0 && (JS.test(tail) || TS.test(tail))) {
        fail(
          `${quote(rel(pkg.dir))}: exports ${quote(subpath)} -> ${quote(target)} matches no built module`,
        );
      }
      for (const file of matches) {
        const expanded = subpath.replace("*", file.slice(head.length, file.length - tail.length));
        const declaration = toDeclaration(file, outputs);
        if (winner(expanded, keys) !== subpath || declaration === undefined) continue;
        if (!outputs.text.has(declaration)) continue;
        found.push({ name: unitName(expanded), declaration });
      }
      continue;
    }
    // Targets without types (package.json, styles) have no API to read.
    if (!(JS.test(target) || TS.test(target))) continue;
    const declaration = toDeclaration(absolute, outputs);
    if (declaration === undefined || !(outputs.text.has(declaration) || isFile(declaration))) {
      const wanted = declaration === undefined ? quote(target) : quote(rel(declaration));
      fail(
        `${quote(rel(pkg.dir))}: exports ${quote(subpath)} -> ${quote(target)}, but no declaration file ${wanted} was built or exists`,
      );
    }
    found.push({ name: unitName(subpath), declaration });
  }
  return found;
}

// Serves the in-memory outputs to a second program, also through symlinked paths such as workspace node_modules.
function virtualHost(
  options: ts.CompilerOptions,
  outputs: Outputs,
  resolutions: Resolutions,
): ts.CompilerHost {
  const host = ts.createCompilerHost(options, true);
  delete host.trace;
  const real = (file: string): string => {
    if (outputs.text.has(file) || outputs.dirs.has(file)) return file;
    let dir = file;
    while (!existsSync(dir)) {
      const up = path.dirname(dir);
      if (up === dir) return file;
      dir = up;
    }
    return path.join(realpathSync(dir), path.relative(dir, file));
  };
  const { fileExists, readFile, directoryExists } = host;
  host.fileExists = (file) => outputs.text.has(real(file)) || fileExists.call(host, file);
  host.readFile = (file) => outputs.text.get(real(file)) ?? readFile.call(host, file);
  host.directoryExists = (dir) =>
    outputs.dirs.has(real(dir)) || (directoryExists?.call(host, dir) ?? false);
  host.realpath = (file) => {
    const resolved = real(file);
    return outputs.text.has(resolved) ? resolved : (ts.sys.realpath?.(file) ?? file);
  };
  host.writeFile = () => {
    throw new Error("the declaration reader must not write files");
  };
  // An emitted .d.ts reuses the build's resolution of the same import in its source, so it resolves exactly as tsc
  // did in the build (paths, references, untyped JS, ambient modules); a compiled source reads as its emitted .d.ts.
  const fromBuild = (
    literal: ts.StringLiteralLike,
    mode: ts.ResolutionMode,
    containing: ts.SourceFile,
  ): ts.ResolvedModuleWithFailedLookupLocations | undefined => {
    const source = outputs.source.get(real(containing.fileName));
    return source && outputs.resolutions.get(source.fileName)?.get(resolutionKey(literal, mode));
  };
  const toDeclarationFile = (
    result: ts.ResolvedModuleWithFailedLookupLocations,
  ): ts.ResolvedModuleWithFailedLookupLocations => {
    const found = result.resolvedModule;
    const declaration = found && outputs.declarationOf.get(real(found.resolvedFileName));
    if (found === undefined || declaration === undefined) return result;
    const extension = declaration.endsWith(".d.mts")
      ? ts.Extension.Dmts
      : declaration.endsWith(".d.cts")
        ? ts.Extension.Dcts
        : ts.Extension.Dts;
    return {
      ...result,
      resolvedModule: {
        ...found,
        resolvedFileName: declaration,
        extension,
        isExternalLibraryImport: false,
      },
    };
  };
  host.resolveModuleNameLiterals = recordingResolver(
    host,
    options,
    resolutions,
    fromBuild,
    toDeclarationFile,
  );
  return host;
}

function read(
  units: Unit[],
  buildOptions: ts.CompilerOptions,
  outputs: Outputs,
  entries: Map<string, Entry>,
  rel: (file: string) => string,
): void {
  const options: ts.CompilerOptions = { ...buildOptions, noEmit: true, skipLibCheck: true };
  const rootNames = new Set([
    ...units.map((u) => u.declaration),
    ...(outputs.declarations.get(buildOptions) ?? []),
  ]);
  const resolutions: Resolutions = new Map();
  const program = ts.createProgram({
    rootNames: [...rootNames],
    options,
    host: virtualHost(options, outputs, resolutions),
  });
  const checker = program.getTypeChecker();
  // The one rule for every import: what tsc made of it in this build.
  const classify = (file: ts.SourceFile, specifier: ts.StringLiteralLike): Imported => {
    const mode = program.getModeForUsageLocation(file, specifier);
    const found = resolutions.get(file.fileName)?.get(resolutionKey(specifier, mode));
    if (found?.resolvedModule !== undefined) {
      const target = program.getSourceFile(found.resolvedModule.resolvedFileName);
      return target === undefined ? { kind: "untyped" } : { kind: "file", nodes: [target] };
    }
    const ambient = checker.getSymbolAtLocation(specifier);
    if (ambient === undefined) return { kind: "unresolved" };
    return { kind: "ambient", nodes: (ambient.declarations ?? []).filter(ts.isModuleDeclaration) };
  };
  // Unresolved imports would silently drop what they re-export. Only what the units reach can drop entries:
  // the files they import and the ambient module blocks they import from.
  const unresolved: { file: string; specifier: string }[] = [];
  const seen = new Set<ts.Node>();
  const queue: ts.Node[] = units.flatMap((u) => program.getSourceFile(u.declaration) ?? []);
  for (let reached = queue.shift(); reached !== undefined; reached = queue.shift()) {
    const file = reached.getSourceFile();
    if (seen.has(reached) || file.fileName.split("/").includes("node_modules")) continue;
    seen.add(reached);
    const visit = (node: ts.Node): void => {
      const specifier = moduleSpecifier(node);
      const imported = specifier === undefined ? undefined : classify(file, specifier);
      if (specifier !== undefined && imported?.kind === "unresolved") {
        unresolved.push({ file: file.fileName, specifier: specifier.text });
      } else if (imported?.kind === "file" || imported?.kind === "ambient") {
        queue.push(...imported.nodes);
      }
      ts.forEachChild(node, visit);
    };
    visit(reached);
  }
  if (unresolved.length > 0) {
    const lines = unresolved.map(
      (u) => `${quote(rel(u.file))}: cannot resolve ${quote(u.specifier)}`,
    );
    fail(`the built declarations import modules the reader cannot resolve:\n${lines.join("\n")}`);
  }
  for (const unit of units) {
    const file = program.getSourceFile(unit.declaration);
    if (file === undefined) fail(`cannot load ${quote(unit.declaration)}`);
    const module = checker.getSymbolAtLocation(file);
    if (module === undefined) continue;
    new Reader(unit.name, checker, outputs, entries, classify).walk(module, "");
  }
}

type Imported =
  { kind: "file" | "ambient"; nodes: ts.Node[] } | { kind: "untyped" } | { kind: "unresolved" };

// The import where tsc lost a name's declaration: follow the whole alias chain (re-exports, local
// import-then-export, barrels) to the hop whose target has no declaration, and return that hop's import.
function importOrigin(
  exported: ts.Symbol,
  checker: ts.TypeChecker,
): { file: ts.SourceFile; specifier: ts.StringLiteral } | undefined {
  const visited = new Set<ts.Symbol>();
  for (let hop = exported; hop.flags & ts.SymbolFlags.Alias && !visited.has(hop);) {
    visited.add(hop);
    const next = checker.getImmediateAliasedSymbol(hop);
    if (next !== undefined && (next.declarations ?? []).length > 0) {
      hop = next;
      continue;
    }
    for (const declaration of hop.declarations ?? []) {
      const specifier = importedFrom(declaration);
      if (specifier !== undefined) return { file: declaration.getSourceFile(), specifier };
    }
    return undefined;
  }
  return undefined;
}

function importedFrom(declaration: ts.Node): ts.StringLiteral | undefined {
  for (let node: ts.Node | undefined = declaration; node !== undefined; node = node.parent) {
    const specifier =
      (ts.isExportDeclaration(node) || ts.isImportDeclaration(node)) && node.moduleSpecifier
        ? node.moduleSpecifier
        : ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)
          ? node.moduleReference.expression
          : undefined;
    if (specifier !== undefined) return ts.isStringLiteral(specifier) ? specifier : undefined;
  }
  return undefined;
}

// The module a declaration imports from; a side-effect import brings in no names, so it is not one.
function moduleSpecifier(node: ts.Node): ts.StringLiteral | undefined {
  let specifier: ts.Node | undefined;
  if (ts.isImportDeclaration(node) && node.importClause !== undefined)
    specifier = node.moduleSpecifier;
  else if (ts.isExportDeclaration(node)) specifier = node.moduleSpecifier;
  else if (ts.isExternalModuleReference(node)) specifier = node.expression;
  else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument))
    specifier = node.argument.literal;
  return specifier !== undefined && ts.isStringLiteral(specifier) ? specifier : undefined;
}

class Reader {
  private readonly unit: string;
  private readonly checker: ts.TypeChecker;
  private readonly outputs: Outputs;
  private readonly entries: Map<string, Entry>;
  private readonly classify: (file: ts.SourceFile, specifier: ts.StringLiteralLike) => Imported;
  private readonly js = new Map<string, ts.SourceFile>();

  constructor(
    unit: string,
    checker: ts.TypeChecker,
    outputs: Outputs,
    entries: Map<string, Entry>,
    classify: (file: ts.SourceFile, specifier: ts.StringLiteralLike) => Imported,
  ) {
    this.unit = unit;
    this.checker = checker;
    this.outputs = outputs;
    this.entries = entries;
    this.classify = classify;
  }

  walk(container: ts.Symbol, parent: string): void {
    for (const exported of this.checker.getExportsOfModule(container)) {
      const target =
        exported.flags & ts.SymbolFlags.Alias ? this.checker.getAliasedSymbol(exported) : exported;
      // tsc types a name from an untyped JS module as any; it has no declaration to print.
      const origin =
        (target.declarations ?? []).length === 0 ? importOrigin(exported, this.checker) : undefined;
      if (origin !== undefined && this.classify(origin.file, origin.specifier).kind === "untyped") {
        this.add(
          parent,
          "alias",
          exported.getName(),
          `any from ${JSON.stringify(origin.specifier.text)}`,
        );
      } else {
        this.symbol(target, exported.getName(), parent);
      }
    }
  }

  private add(parent: string, kind: string, name: string, signature: string): void {
    const key = JSON.stringify([this.unit, parent, kind, name]);
    const existing = this.entries.get(key);
    if (existing === undefined)
      this.entries.set(key, { unit: this.unit, parent, kind, name, signature });
    else existing.signature += `\n${signature}`;
  }

  private symbol(symbol: ts.Symbol, name: string, parent: string): void {
    const declarations = symbol.declarations ?? [];
    if (declarations.length === 0)
      fail(`unit ${quote(this.unit)}: export ${quote(name)} does not resolve to a declaration`);
    const path = parent === "" ? name : `${parent}.${name}`;
    const single = declarations.filter(ts.isFunctionDeclaration).length === 1;
    let namespace = false;
    for (const d of declarations) {
      if (ts.isSourceFile(d) || ts.isModuleDeclaration(d)) {
        if (namespace) continue;
        namespace = true;
        this.add(parent, "namespace", name, `namespace ${name}`);
        this.walk(symbol, path);
      } else if (ts.isFunctionDeclaration(d)) {
        this.add(parent, "function", name, print(single ? this.withDefaults(d) : d));
      } else if (ts.isClassDeclaration(d)) {
        const header = ts.factory.updateClassDeclaration(
          d,
          kept(d.modifiers),
          d.name,
          d.typeParameters,
          d.heritageClauses,
          [],
        );
        this.add(parent, "class", name, print(header).replace(/\s*\{\s*\}$/, ""));
        this.members(d, path);
      } else if (ts.isInterfaceDeclaration(d)) {
        const header = ts.factory.updateInterfaceDeclaration(
          d,
          kept(d.modifiers),
          d.name,
          d.typeParameters,
          d.heritageClauses,
          [],
        );
        this.add(parent, "interface", name, print(header).replace(/\s*\{\s*\}$/, ""));
        this.members(d, path);
      } else if (ts.isTypeAliasDeclaration(d)) {
        this.add(parent, "type", name, print(d));
      } else if (ts.isEnumDeclaration(d)) {
        this.add(parent, "enum", name, print(d));
      } else if (ts.isVariableDeclaration(d)) {
        const flags = ts.getCombinedNodeFlags(d);
        const kind =
          flags & ts.NodeFlags.Const ? "const" : flags & ts.NodeFlags.Let ? "let" : "var";
        this.add(parent, kind, name, `${kind} ${print(this.withDefaults(d))}`);
      } else {
        this.add(parent, ts.SyntaxKind[d.kind], name, print(d));
      }
    }
  }

  private members(owner: ts.ClassDeclaration | ts.InterfaceDeclaration, parent: string): void {
    const counts = new Map<string, number>();
    const keyOf = (m: ts.ClassElement | ts.TypeElement): string =>
      `${m.kind}:${isStatic(m)}:${memberName(m)}`;
    for (const m of owner.members) counts.set(keyOf(m), (counts.get(keyOf(m)) ?? 0) + 1);
    for (const m of owner.members) {
      if (m.name !== undefined && ts.isPrivateIdentifier(m.name)) continue;
      if (ts.getCombinedModifierFlags(m as ts.Declaration) & ts.ModifierFlags.Private) continue;
      const kind = memberKind(m);
      if (kind === undefined) continue;
      const shown = counts.get(keyOf(m)) === 1 ? this.withDefaults(m, owner) : m;
      this.add(parent, isStatic(m) ? `static ${kind}` : kind, memberName(m), print(shown));
    }
  }

  // Default values are not in the .d.ts: read them from the .js the same build emitted for the same source.
  private withDefaults<T extends ts.Node>(
    node: T,
    owner?: ts.ClassDeclaration | ts.InterfaceDeclaration,
  ): T {
    const fn = jsCounterpart(node, owner, this.jsFor(node));
    const parameters = signatureParameters(node);
    if (fn === undefined || parameters === undefined || fn.parameters.length !== parameters.length)
      return node;
    let changed = false;
    const updated = parameters.map((p, i) => {
      const initializer = fn.parameters[i]?.initializer;
      if (initializer === undefined) return p;
      changed = true;
      const value = ts.factory.createIdentifier(print(initializer));
      return ts.factory.updateParameterDeclaration(
        p,
        p.modifiers,
        p.dotDotDotToken,
        p.name,
        undefined,
        p.type,
        value,
      );
    });
    return changed ? replaceParameters(node, updated) : node;
  }

  private jsFor(node: ts.Node): ts.SourceFile | undefined {
    const source = this.outputs.source.get(node.getSourceFile().fileName);
    const file = source === undefined ? undefined : this.outputs.jsOf.get(source.fileName);
    const text = file === undefined ? undefined : this.outputs.text.get(file);
    if (file === undefined || text === undefined) return undefined;
    let parsed = this.js.get(file);
    if (parsed === undefined) {
      parsed = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
      this.js.set(file, parsed);
    }
    return parsed;
  }
}

type FunctionLike =
  | ts.FunctionDeclaration
  | ts.MethodDeclaration
  | ts.ConstructorDeclaration
  | ts.ArrowFunction
  | ts.FunctionExpression;

function signatureParameters(node: ts.Node): ts.NodeArray<ts.ParameterDeclaration> | undefined {
  if (
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node)
  )
    return node.parameters;
  if (ts.isVariableDeclaration(node) && node.type !== undefined && ts.isFunctionTypeNode(node.type))
    return node.type.parameters;
  return undefined;
}

function replaceParameters<T extends ts.Node>(node: T, parameters: ts.ParameterDeclaration[]): T {
  const f = ts.factory;
  if (ts.isFunctionDeclaration(node)) {
    return f.updateFunctionDeclaration(
      node,
      node.modifiers,
      node.asteriskToken,
      node.name,
      node.typeParameters,
      parameters,
      node.type,
      node.body,
    ) as ts.Node as T;
  }
  if (ts.isMethodDeclaration(node)) {
    return f.updateMethodDeclaration(
      node,
      node.modifiers,
      node.asteriskToken,
      node.name,
      node.questionToken,
      node.typeParameters,
      parameters,
      node.type,
      node.body,
    ) as ts.Node as T;
  }
  if (ts.isConstructorDeclaration(node)) {
    return f.updateConstructorDeclaration(
      node,
      node.modifiers,
      parameters,
      node.body,
    ) as ts.Node as T;
  }
  if (
    ts.isVariableDeclaration(node) &&
    node.type !== undefined &&
    ts.isFunctionTypeNode(node.type)
  ) {
    const type = f.updateFunctionTypeNode(
      node.type,
      node.type.typeParameters,
      f.createNodeArray(parameters),
      node.type.type,
    );
    return f.updateVariableDeclaration(
      node,
      node.name,
      node.exclamationToken,
      type,
      node.initializer,
    ) as ts.Node as T;
  }
  return node;
}

function nameText(name: ts.Node | undefined): string | undefined {
  return name !== undefined && (ts.isIdentifier(name) || ts.isStringLiteral(name))
    ? name.text
    : undefined;
}

// Finds the emitted JS function for a top-level function, a const holding one, or a class constructor or method.
function jsCounterpart(
  node: ts.Node,
  owner: ts.ClassDeclaration | ts.InterfaceDeclaration | undefined,
  js: ts.SourceFile | undefined,
): FunctionLike | undefined {
  if (js === undefined) return undefined;
  const topLevel = (decl: ts.Node): boolean =>
    ts.isSourceFile(decl.parent) ||
    (ts.isVariableDeclarationList(decl.parent) && ts.isSourceFile(decl.parent.parent.parent));
  if (owner !== undefined) {
    if (!ts.isClassDeclaration(owner) || !topLevel(owner)) return undefined;
    const name = nameText(owner.name);
    const cls = js.statements.find(
      (s): s is ts.ClassDeclaration =>
        ts.isClassDeclaration(s) && nameText(s.name) === name && isDefault(s) === isDefault(owner),
    );
    if (cls === undefined) return undefined;
    if (ts.isConstructorDeclaration(node)) return cls.members.find(ts.isConstructorDeclaration);
    if (!ts.isMethodDeclaration(node)) return undefined;
    return cls.members.find(
      (m): m is ts.MethodDeclaration =>
        ts.isMethodDeclaration(m) &&
        nameText(m.name) === nameText(node.name) &&
        isStatic(m) === isStatic(node),
    );
  }
  if (!topLevel(node)) return undefined;
  if (ts.isFunctionDeclaration(node)) {
    const name = nameText(node.name);
    return js.statements.find(
      (s): s is ts.FunctionDeclaration =>
        ts.isFunctionDeclaration(s) &&
        s.body !== undefined &&
        nameText(s.name) === name &&
        isDefault(s) === isDefault(node),
    );
  }
  if (ts.isVariableDeclaration(node)) {
    for (const statement of js.statements) {
      if (!ts.isVariableStatement(statement)) continue;
      const decl = statement.declarationList.declarations.find(
        (d) => nameText(d.name) === nameText(node.name),
      );
      const init = decl?.initializer;
      if (init !== undefined && (ts.isArrowFunction(init) || ts.isFunctionExpression(init)))
        return init;
    }
  }
  return undefined;
}

function isDefault(node: ts.Node): boolean {
  return (ts.getCombinedModifierFlags(node as ts.Declaration) & ts.ModifierFlags.Default) !== 0;
}

function isStatic(node: ts.Node): boolean {
  return (ts.getCombinedModifierFlags(node as ts.Declaration) & ts.ModifierFlags.Static) !== 0;
}

function memberKind(m: ts.ClassElement | ts.TypeElement): string | undefined {
  if (ts.isConstructorDeclaration(m)) return "constructor";
  if (ts.isMethodDeclaration(m) || ts.isMethodSignature(m)) return "method";
  if (ts.isPropertyDeclaration(m) || ts.isPropertySignature(m)) return "property";
  if (ts.isGetAccessorDeclaration(m)) return "get";
  if (ts.isSetAccessorDeclaration(m)) return "set";
  if (ts.isCallSignatureDeclaration(m)) return "call";
  if (ts.isConstructSignatureDeclaration(m)) return "construct";
  if (ts.isIndexSignatureDeclaration(m)) return "index";
  return undefined;
}

function memberName(m: ts.ClassElement | ts.TypeElement): string {
  if (ts.isConstructorDeclaration(m)) return "constructor";
  if (m.name === undefined) return "";
  return nameText(m.name) ?? printer.printNode(ts.EmitHint.Unspecified, m.name, m.getSourceFile());
}

function kept(modifiers: ts.NodeArray<ts.ModifierLike> | undefined): ts.ModifierLike[] {
  return (modifiers ?? []).filter((m) => !DROPPED_MODIFIERS.has(m.kind));
}

function singleLine(node: ts.Node): void {
  ts.setEmitFlags(node, ts.EmitFlags.SingleLine);
  ts.forEachChild(node, singleLine);
}

// One line per signature, whatever the layout: type literals and tuples print on one line, other line breaks become spaces.
function print(node: ts.Node): string {
  const source = ts.getOriginalNode(node).getSourceFile();
  const bare =
    ts.canHaveModifiers(node) && ts.isStatement(node)
      ? ts.factory.replaceModifiers(node, kept(node.modifiers) as ts.Modifier[])
      : node;
  singleLine(bare);
  return printer
    .printNode(ts.EmitHint.Unspecified, bare, source)
    .replace(/\n\s*/g, " ")
    .replace(/;$/, "");
}

function main(args: string[]): number {
  const [project, out] = args;
  if (
    args.length !== 2 ||
    project === undefined ||
    out === undefined ||
    project === "" ||
    out === ""
  ) {
    console.error(`${PREFIX}usage: api-snapshot.ts <project-dir> <entries-file>`);
    return 2;
  }
  try {
    const entries = generate(project);
    writeFileSync(out, `${JSON.stringify(entries, null, 2)}\n`);
    console.log(`${PREFIX}${entries.length} entries`);
    return 0;
  } catch (error) {
    const message =
      error instanceof Failure
        ? error.message
        : `unexpected error: ${quote(error instanceof Error ? (error.stack ?? error.message) : String(error))}`;
    for (const line of message.split("\n")) console.error(`${PREFIX}${line}`);
    return 2;
  }
}

const entry = process.argv[1];
if (entry !== undefined && realpathSync(entry) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
