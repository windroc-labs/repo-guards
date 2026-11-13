// Preloaded into dependency-cruiser with --import. Its config, extends, webpack and babel options can name JavaScript files,
// which it imports; this refuses every module outside this directory, so the project's code never runs here.
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)) + path.sep;

registerHooks({
  resolve(specifier, context, nextResolve) {
    const resolved = nextResolve(specifier, context);
    if (resolved.url.startsWith("file:") && !fileURLToPath(resolved.url).startsWith(HERE)) {
      throw new Error(
        `layer-rules: refusing to load ${fileURLToPath(resolved.url)}: it would run the project's code`,
      );
    }
    return resolved;
  },
});

// dependency-cruiser exits with its error count, which the OS takes modulo 256.
process.on("exit", (code) => {
  if (code !== 0) process.exitCode = 1;
});
