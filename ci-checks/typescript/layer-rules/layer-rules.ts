// The Layer rules step: runs dependency-cruiser over the project in the current directory with its
// .dependency-cruiser.json; skips when there is no config. Exit 0 pass or skip, 1 fail.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const CONFIG = ".dependency-cruiser.json";
const SCRIPT_CONFIGS = [".js", ".cjs", ".mjs", ".ts", ".cts", ".mts"].map(
  (ext) => `.dependency-cruiser${ext}`,
);
const BASELINE = ".dependency-cruiser-known-violations.json";
const GUARD = fileURLToPath(new URL("guard.ts", import.meta.url));
const BIN = fileURLToPath(
  new URL("node_modules/dependency-cruiser/bin/dependency-cruiser.mjs", import.meta.url),
);

function main(): number {
  if (!existsSync(CONFIG)) {
    const script = SCRIPT_CONFIGS.find((name) => existsSync(name));
    if (script === undefined) {
      console.log(
        `Layer rules: no dependency-cruiser config here (${[CONFIG, ...SCRIPT_CONFIGS].join(", ")}); skipped.`,
      );
      return 0;
    }
    console.log(
      `Layer rules: ${script} would run code when loaded; write the rules as ${CONFIG} instead.`,
    );
    return 1;
  }
  const args = ["--import", GUARD, BIN, "--config", CONFIG, "--no-cache", "--output-type", "err"];
  if (existsSync(BASELINE)) args.push("--ignore-known", BASELINE);
  const done = spawnSync(process.execPath, [...args, "."], { stdio: "inherit" });
  return done.status === 0 ? 0 : 1;
}

process.exitCode = main();
