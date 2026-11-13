// Preloaded into dependency-cruiser with --import. Its config, extends, webpack and babel options can name JavaScript files,
// which it imports. Only this directory's modules and files CODEOWNERS locks (.dependency-cruiser*, at any depth) may load,
// so no unreviewed project code runs here.
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)) + path.sep;
const LOCKED = ".dependency-cruiser";

registerHooks({
  resolve(specifier, context, nextResolve) {
    const resolved = nextResolve(specifier, context);
    if (!resolved.url.startsWith("file:")) return resolved;
    const file = fileURLToPath(resolved.url);
    if (file.startsWith(HERE) || path.basename(file).startsWith(LOCKED)) return resolved;
    throw new Error(
      `layer-rules: refusing to load ${file}: only files named ${LOCKED}* may run, as CODEOWNERS locks them`,
    );
  },
});

// dependency-cruiser exits with its error count, which the OS takes modulo 256.
process.on("exit", (code) => {
  if (code !== 0) process.exitCode = 1;
});
