import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const USAGE = `usage: workflow-config.ts tsc-flags TSCONFIG
       workflow-config.ts knip-config KNIP_JSON ENTRY_JSON PROJECT_JSON OUTPUT
       workflow-config.ts license-allow RESOLVE_DIR LICENSES_TOML
       workflow-config.ts coverage-include PROJECT_JSON
       workflow-config.ts coverage-exclude COVERAGE_JSON
       workflow-config.ts replace FILE FROM TO`;

type Out = (line: string) => void;

class UsageError extends Error {}

const readJson = (file: string): any => JSON.parse(readFileSync(file, "utf8"));

const COMMANDS: Record<string, [number, (args: string[], out: Out) => void]> = {
  "tsc-flags": [
    1,
    ([tsconfig], out) => {
      for (const [key, value] of Object.entries(readJson(tsconfig!).compilerOptions)) out(`--${key}\n${value}`);
    },
  ],
  "knip-config": [
    4,
    ([src, entry, project, output]) => {
      const config = readJson(src!);
      config.entry = JSON.parse(entry!);
      config.project = JSON.parse(project!);
      writeFileSync(output!, JSON.stringify(config));
    },
  ],
  // smol-toml is installed with the comment-budget checker, so it is loaded from there.
  "license-allow": [
    2,
    ([resolveDir, licenses], out) => {
      const { parse } = createRequire(path.join(path.resolve(resolveDir!), "noop.js"))("smol-toml");
      out(parse(readFileSync(licenses!, "utf8")).allow.join(";"));
    },
  ],
  "coverage-include": [
    1,
    ([project], out) => {
      for (const glob of JSON.parse(project!)) if (glob.endsWith("!")) out(glob.slice(0, -1));
    },
  ],
  "coverage-exclude": [
    1,
    ([coverage], out) => {
      for (const glob of readJson(coverage!).exclude) out(glob);
    },
  ],
  replace: [
    3,
    ([file, from, to]) => {
      writeFileSync(file!, readFileSync(file!, "utf8").split(from!).join(to!));
    },
  ],
};

export function run(argv: readonly string[], out: Out): void {
  const [command = "", ...args] = argv;
  const spec = Object.hasOwn(COMMANDS, command) ? COMMANDS[command] : undefined;
  if (spec === undefined || args.length !== spec[0]) throw new UsageError(USAGE);
  spec[1](args, out);
}

function main(): number {
  try {
    run(process.argv.slice(2), (line) => console.log(line));
    return 0;
  } catch (error) {
    console.error(error instanceof UsageError ? error.message : `workflow-config: ${String(error)}`);
    return 2;
  }
}

const entry = process.argv[1];
if (entry !== undefined && realpathSync(entry) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
