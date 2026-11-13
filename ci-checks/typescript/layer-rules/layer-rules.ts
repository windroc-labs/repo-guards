// The Layer rules step: runs dependency-cruiser over the project in the current directory with its config;
// skips when there is none. Exit 0 pass or skip, 1 fail.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// dependency-cruiser's own search order.
const CONFIGS = [".json", ".js", ".cjs", ".mjs", ".ts", ".cts", ".mts"].map(
  (ext) => `.dependency-cruiser${ext}`,
);
const BASELINE = ".dependency-cruiser-known-violations.json";
const GUARD = fileURLToPath(new URL("guard.ts", import.meta.url));
const BIN = fileURLToPath(
  new URL("node_modules/dependency-cruiser/bin/dependency-cruiser.mjs", import.meta.url),
);

function main(): number {
  const config = CONFIGS.find((name) => existsSync(name));
  if (config === undefined) {
    console.log(`Layer rules: no dependency-cruiser config here (${CONFIGS.join(", ")}); skipped.`);
    return 0;
  }
  const args = ["--import", GUARD, BIN, "--config", config, "--no-cache", "--output-type", "err"];
  if (existsSync(BASELINE)) args.push("--ignore-known", BASELINE);
  const done = spawnSync(process.execPath, [...args, "."], { stdio: "inherit" });
  return done.status === 0 ? 0 : 1;
}

process.exitCode = main();
