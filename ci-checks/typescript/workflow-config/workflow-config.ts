import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const USAGE = `usage: workflow-config.ts tsc-flags TSCONFIG
       workflow-config.ts coverage-include KNIP_JSON
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
  "coverage-include": [
    1,
    ([knip], out) => {
      for (const glob of readJson(knip!).project) if (glob.endsWith("!")) out(glob.slice(0, -1));
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
